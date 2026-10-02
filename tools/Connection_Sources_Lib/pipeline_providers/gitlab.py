import io
import os
import time
import zipfile
from typing import Optional
from urllib.parse import quote, urlparse

import requests

from Talan_Library.Connection_Sources_Lib.pipeline_providers.base import PipelineProvider

API = "/api/v4"

# Pipeline statuses that mean "still in the queue" vs. "running". Anything
# else (success, failed, canceled, skipped, manual, scheduled) is final for
# this run: a "manual" pipeline is blocked on a human and won't finish alone.
QUEUED = {"created", "waiting_for_resource", "preparing", "pending"}
RUNNING = {"running", "canceling"}


class GitLabProvider(PipelineProvider):
    """
    GitLab CI pipeline for one project and ref. The pipeline runs on whatever
    GitLab Runner the project uses; the runner itself is never addressed.

    --url is the GitLab host (https://gitlab.example.com) or a project URL
    (https://gitlab.example.com/group/app). --project (numeric id or
    group/path) wins over a project path taken from the URL.
    """

    name = "gitlab"
    token_vars = ("QA_GITLAB_TOKEN", "GITLAB_TOKEN")
    required_args = ("url", "ref")

    def __init__(
        self,
        url: str,
        ref: str,
        token: str,
        project: Optional[str] = None,
        timeout_s: float = 900.0,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.base_url, url_project = self.parse_gitlab_url(url)
        self.project = str(project or url_project or "")
        if not self.project:
            raise ValueError(
                f"No GitLab project in {url!r}: pass --project (numeric id or group/path) "
                "or a project URL like https://gitlab.example.com/group/app"
            )
        self.ref = ref
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.session = requests.Session()
        self.session.headers["PRIVATE-TOKEN"] = token
        self._project_path = None

    @staticmethod
    def parse_gitlab_url(url: str) -> tuple[str, Optional[str]]:
        """https://gitlab.example.com/group/app/-/pipelines
        -> ("https://gitlab.example.com", "group/app")."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Not a valid absolute URL: {url!r}")
        path = parsed.path.split("/-/", 1)[0].strip("/")
        if path.endswith(".git"):
            path = path[: -len(".git")]
        return f"{parsed.scheme}://{parsed.netloc}", path or None

    @staticmethod
    def detect(url: str) -> bool:
        parsed = urlparse(url or "")
        return "gitlab" in parsed.netloc.lower() or "/-/pipelines" in parsed.path

    @classmethod
    def from_args(cls, args, token: str) -> "GitLabProvider":
        return cls(url=args.url, ref=args.ref, token=token, project=args.project)

    # ---- HTTP -------------------------------------------------------------

    def _api(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{API}/projects/{quote(self.project, safe='')}{path}"
        resp = self.session.request(method, url, timeout=60, **kwargs)
        if resp.status_code in (401, 403):
            raise PermissionError(
                f"GitLab refused the request ({resp.status_code}) on {self.base_url}. "
                f"Check that {' / '.join(self.token_vars)} has 'api' scope on project {self.project}."
            )
        if resp.status_code == 404:
            raise LookupError(f"GitLab returned 404 for {path or 'the project'} on project {self.project}.")
        resp.raise_for_status()
        return resp

    # ---- PipelineProvider -------------------------------------------------

    @property
    def pipeline_name(self) -> str:
        if self._project_path is None:
            if self.project.isdigit():
                self._project_path = self._api("GET", "").json().get("path_with_namespace", self.project)
            else:
                self._project_path = self.project
        return self._project_path

    def trigger_and_wait(self, params: dict) -> dict:
        payload = {"ref": self.ref}
        if params:
            payload["variables"] = [{"key": str(k), "value": str(v)} for k, v in params.items()]
        pipeline = self._api("POST", "/pipeline", json=payload).json()
        pipeline_id = pipeline["id"]

        # Two phases, each with its own timeout, like Jenkins: queue, then run.
        for waiting_on in (QUEUED, RUNNING):
            deadline = time.time() + self.timeout_s
            while pipeline.get("status") in waiting_on:
                if time.time() >= deadline:
                    phase = "leave the queue" if waiting_on is QUEUED else "finish"
                    raise TimeoutError(f"Pipeline #{pipeline_id} did not {phase} in time.")
                time.sleep(self.poll_interval_s)
                pipeline = self._api("GET", f"/pipelines/{pipeline_id}").json()

        status = pipeline.get("status")
        return {
            "provider": self.name,
            "pipeline_name": self.pipeline_name,
            "build_id": pipeline_id,
            "status": status,
            "succeeded": status == "success",
            "duration_s": pipeline.get("duration"),
            "params_used": dict(params or {}),
            "url": pipeline.get("web_url"),
            "tests": self._test_report(pipeline_id),
            "ref": self.ref,
        }

    def _test_report(self, pipeline_id) -> Optional[dict]:
        """GitLab's own JUnit summary for the pipeline (jobs must publish
        artifacts:reports:junit). None when no job published a report."""
        try:
            report = self._api("GET", f"/pipelines/{pipeline_id}/test_report").json()
        except (LookupError, requests.HTTPError):
            return None
        if not report.get("total_count"):
            return None
        failing = [
            f"{suite.get('name')}::{_case_name(case)}"
            for suite in report.get("test_suites", [])
            for case in suite.get("test_cases", [])
            if case.get("status") in ("failed", "error")
        ]
        return {
            "passed": report.get("success_count", 0),
            "failed": report.get("failed_count", 0) + report.get("error_count", 0),
            "skipped": report.get("skipped_count", 0),
            "failing_cases": failing,
        }

    def collect_artifacts(
        self,
        build_id,
        dest_dir: str,
        extensions: Optional[tuple[str, ...]] = None,
    ) -> tuple[list[dict], list[str]]:
        """Each job's artifact archive is unzipped under dest_dir/<job name>/."""
        jobs = self._api("GET", f"/pipelines/{build_id}/jobs", params={"per_page": 100}).json()

        artifacts, saved_paths = [], []
        for job in jobs:
            if not job.get("artifacts_file"):
                continue
            archive = self._api("GET", f"/jobs/{job['id']}/artifacts", allow_redirects=True).content
            job_dir = os.path.join(dest_dir, _safe(job.get("name") or str(job["id"])))
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    rel_path = member.filename
                    artifacts.append({
                        "job": job.get("name"),
                        "fileName": os.path.basename(rel_path),
                        "relativePath": rel_path,
                    })
                    if extensions and not rel_path.lower().endswith(extensions):
                        continue
                    local_path = os.path.abspath(os.path.join(job_dir, rel_path))
                    if not local_path.startswith(os.path.abspath(job_dir) + os.sep):
                        continue  # a zip entry trying to escape job_dir
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    with zf.open(member) as src, open(local_path, "wb") as dst:
                        dst.write(src.read())
                    saved_paths.append(local_path)
        return artifacts, saved_paths


def _case_name(case: dict) -> str:
    if case.get("classname"):
        return f"{case['classname']}.{case.get('name')}"
    return str(case.get("name"))


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
