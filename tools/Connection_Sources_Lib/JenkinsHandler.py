import os
import time
from typing import Optional
from urllib.parse import quote, urlparse

import jenkins  # pip install python-jenkins
import requests


class JenkinsHandler:
    """
    Encapsulates a single Jenkins job (base_url + job_full_name + credentials)
    and exposes trigger/poll/console/artifact operations as methods, so the
    caller doesn't have to keep repeating job_url/user/token on every call.
    """

    def __init__(self, job_url: str, user: str, token: str) -> None:
        """
        Args:
            job_url: Full URL to a Jenkins job (including nested folders),
                e.g. "https://ci.example.com/job/platform/job/regression-suite/".
            user: Jenkins username used for authentication.
            token: Jenkins API token (or password) used for authentication.

        Raises:
            ValueError: If job_url is not an absolute URL, or if no "job"
                segments could be found in its path.
        """
        self.job_url = job_url
        self.user = user
        self.token = token
        self.base_url, self.job_name = self.parse_jenkins_job_url(job_url)
        self.server = jenkins.Jenkins(self.base_url, username=user, password=token)

    @staticmethod
    def parse_jenkins_job_url(job_url: str) -> tuple[str, str]:
        """
        Turns any Jenkins job URL (including nested folders) into
        (base_url, job_full_name) for use with python-jenkins.

        e.g. https://ci.example.com/job/platform/job/regression-suite/
             -> ("https://ci.example.com", "platform/regression-suite")

        Args:
            job_url: Full URL to a Jenkins job.

        Returns:
            A tuple of (base_url, job_full_name) where base_url is the
            scheme+host (e.g. "https://ci.example.com") and job_full_name is
            the "/"-joined path of job segments (e.g. "platform/regression-suite").

        Raises:
            ValueError: If job_url is not an absolute URL, or if no "job"
                segments could be found in its path.
        """
        parsed = urlparse(job_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Not a valid absolute URL: {job_url!r}")

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        segments = [s for s in parsed.path.split("/") if s]

        names = []
        it = iter(segments)
        for seg in it:
            if seg == "job":
                try:
                    names.append(next(it))
                except StopIteration:
                    break

        if not names:
            raise ValueError(f"Could not find a job name in URL path {parsed.path!r}")

        return base_url, "/".join(names)

    def trigger_pipeline(
        self,
        params: Optional[dict] = None,
        wait: bool = True,
        timeout_s: float = 900.0,
        poll_interval_s: float = 3.0,
    ) -> dict:
        """
        Trigger this Jenkins job. `params` is passed through as-is:
        omit/None/{} -> plain /build endpoint; a non-empty dict -> /buildWithParameters
        with exactly those values. No auto-detection, no merging with job defaults —
        the caller is responsible for sending whatever the job requires.

        If wait=False, returns immediately with just the queue_id so you can
        track/poll it yourself.

        Args:
            params: Build parameters to send. None/{} triggers a plain build;
                a non-empty dict triggers a parameterized build with exactly
                these values.
            wait: If True (default), blocks until the build leaves the queue
                and finishes running. If False, returns immediately after
                queuing with just the queue_id.
            timeout_s: Maximum seconds to wait for the build to leave the
                queue, and separately, the maximum seconds to wait for the
                build to finish once running. Only used when wait=True.
            poll_interval_s: Seconds to sleep between status checks while
                polling the queue and the running build. Only used when
                wait=True.

        Returns:
            If wait=False: {"job_url", "queue_id", "status": "queued"}.
            If wait=True: a dict with "job_url", "build_number", "status",
            "duration_s", "params_used", "console_url", and "tests" (None if
            no test report was available, otherwise a dict with "passed",
            "failed", "skipped", and "failing_cases").

        Raises:
            RuntimeError: If the build is cancelled while still queued.
            TimeoutError: If the build doesn't leave the queue, or doesn't
                finish running, within timeout_s.
        """
        queue_id = self.server.build_job(self.job_name, parameters=params or None)

        if not wait:
            return {"job_url": self.job_url, "queue_id": queue_id, "status": "queued"}

        # Poll queue -> build number
        deadline = time.time() + timeout_s
        build_number = None
        while time.time() < deadline:
            item = self.server.get_queue_item(queue_id)
            if item.get("cancelled"):
                raise RuntimeError("Build was cancelled while queued.")
            executable = item.get("executable")
            if executable:
                build_number = executable["number"]
                break
            time.sleep(poll_interval_s)
        if build_number is None:
            raise TimeoutError("Timed out waiting for build to leave the queue.")

        # Poll for completion
        deadline = time.time() + timeout_s
        info = None
        while time.time() < deadline:
            info = self.server.get_build_info(self.job_name, build_number)
            if not info.get("building"):
                break
            time.sleep(poll_interval_s)
        else:
            raise TimeoutError(f"Build #{build_number} did not finish in time.")

        # Optional test report
        try:
            report = self.server.get_build_test_report(self.job_name, build_number)
        except jenkins.JenkinsException:
            report = None

        result = {
            "job_url": self.job_url,
            "build_number": build_number,
            "status": info.get("result"),
            "duration_s": round(info.get("duration", 0) / 1000, 1),
            "params_used": params or {},
            "console_url": info.get("url"),
            "tests": None,
        }

        if report is not None:
            failing = [
                f"{s.get('name')}::{c.get('name')}"
                for s in report.get("suites", [])
                for c in s.get("cases", [])
                if c.get("status") in ("FAILED", "REGRESSION")
            ]
            result["tests"] = {
                "passed": report.get("passCount", 0),
                "failed": report.get("failCount", 0),
                "skipped": report.get("skipCount", 0),
                "failing_cases": failing,
            }

        return result

    def get_console_log(self, build_number: int) -> str:
        """
        Fetch the full console output for a given build.

        Args:
            build_number: The build number whose console output to fetch.

        Returns:
            The full console log text for the given build.
        """
        return self.server.get_build_console_output(self.job_name, build_number)

    def list_artifacts(self, build_number: int) -> list[dict]:
        """
        Returns whatever artifacts Jenkins recorded for this build, using the
        relativePath Jenkins itself reports — no hardcoded folder name, so this
        works no matter what a given team calls their reports directory
        (e.g. "reports/", "test-output/", "allure-results/", etc.).

        Each item looks like:
            {"fileName": "report.html", "relativePath": "reports/html/report.html", "displayPath": "..."}

        Args:
            build_number: The build number whose artifacts to list.

        Returns:
            A list of artifact dicts as reported by Jenkins (each containing
            at least "fileName", "relativePath", and "displayPath"). Empty
            list if the build recorded no artifacts.
        """
        info = self.server.get_build_info(self.job_name, build_number)
        return info.get("artifacts", [])

    def download_artifacts(
        self,
        build_number: int,
        dest_dir: str,
        extensions: Optional[tuple[str, ...]] = None,
    ) -> list[str]:
        """
        Downloads every artifact Jenkins recorded for the build, preserving
        their relativePath under dest_dir. Optionally filter by extension
        (e.g. extensions=(".xml", ".html", ".json")) if you only want certain
        report types regardless of which folder a team put them in.

        Args:
            build_number: The build number whose artifacts to download.
            dest_dir: Local directory under which artifacts are saved,
                preserving each artifact's relativePath.
            extensions: If given, only artifacts whose relativePath ends with
                one of these suffixes (case-insensitive) are downloaded.
                None (default) downloads everything.

        Returns:
            The list of local file paths that were written.

        Raises:
            requests.HTTPError: If downloading any artifact returns a
                non-success HTTP status.
        """
        artifacts = self.list_artifacts(build_number)

        saved_paths = []
        for art in artifacts:
            rel_path = art["relativePath"]
            if extensions and not rel_path.lower().endswith(extensions):
                continue

            # Jenkins artifact download URL format:
            # <base>/job/<job/path>/<build_number>/artifact/<relativePath>
            job_path_segments = "/job/".join(self.job_name.split("/"))
            artifact_url = (
                f"{self.base_url}/job/{job_path_segments}/{build_number}/artifact/"
                f"{quote(rel_path)}"
            )

            # relativePath comes from the server: never let it escape dest_dir.
            dest_root = os.path.realpath(dest_dir)
            local_path = os.path.realpath(os.path.join(dest_root, rel_path))
            if os.path.commonpath([dest_root, local_path]) != dest_root:
                raise ValueError(f"Refusing artifact path outside {dest_dir!r}: {rel_path!r}")

            resp = requests.get(
                artifact_url, auth=(self.user, self.token), stream=True, timeout=60
            )
            resp.raise_for_status()

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            saved_paths.append(local_path)

        return saved_paths

    def run_pipeline_and_collect_artifacts(
        self,
        params: Optional[dict] = None,
        dest_dir: str = "./downloaded_artifacts",
        extensions: Optional[tuple[str, ...]] = None,
        wait: bool = True,
        timeout_s: float = 900.0,
        poll_interval_s: float = 3.0,
    ) -> dict:
        """
        High-level helper that ties together trigger_pipeline, get_console_log,
        list_artifacts and download_artifacts for this job.

        If wait=False, only the job is triggered and the queued result is
        returned (no console log / artifacts, since there's no build yet).

        Args:
            params: Build parameters to send; see trigger_pipeline for details.
            dest_dir: Local directory under which downloaded artifacts are
                saved, preserving each artifact's relativePath.
            extensions: If given, only download artifacts whose relativePath
                ends with one of these suffixes. None downloads everything.
            wait: If False, only triggers the build and returns the queued
                result (no console log/artifacts are fetched). If True
                (default), waits for the build to finish and then also fetches
                the console log, artifact list, and downloads.
            timeout_s: Maximum seconds to wait for the build to leave the
                queue, and separately, for it to finish running. Only used
                when wait=True.
            poll_interval_s: Seconds to sleep between status checks while
                polling. Only used when wait=True.

        Returns:
            If wait=False: the queued result from trigger_pipeline.
            If wait=True: the trigger_pipeline result dict extended with
            "console" (str), "artifacts" (list[dict]), and "downloaded_files"
            (list[str]).

        Raises:
            RuntimeError: If the build is cancelled while still queued.
            TimeoutError: If the build doesn't leave the queue, or doesn't
                finish running, within timeout_s.
            requests.HTTPError: If downloading any artifact returns a
                non-success HTTP status.
        """
        result = self.trigger_pipeline(
            params=params,
            wait=wait,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

        if not wait:
            return result

        build_number = result["build_number"]

        result["console"] = self.get_console_log(build_number)
        result["artifacts"] = self.list_artifacts(build_number)
        result["downloaded_files"] = self.download_artifacts(build_number, dest_dir, extensions)

        return result


