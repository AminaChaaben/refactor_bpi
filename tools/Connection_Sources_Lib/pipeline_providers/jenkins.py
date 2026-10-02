from typing import Optional

from Talan_Library.Connection_Sources_Lib.JenkinsHandler import JenkinsHandler
from Talan_Library.Connection_Sources_Lib.pipeline_providers.base import PipelineProvider


class JenkinsProvider(PipelineProvider):
    """Jenkins job, e.g. http://localhost:8080/job/regression-suite/
    (nested folders like /job/team/job/app/ work too)."""

    name = "jenkins"
    token_vars = ("QA_JENKINS_TOKEN", "JENKINS_TOKEN")
    required_args = ("url", "user")

    def __init__(self, job_url: str, user: str, token: str) -> None:
        self.handler = JenkinsHandler(job_url=job_url, user=user, token=token)

    @staticmethod
    def detect(url: str) -> bool:
        return "/job/" in (url or "")

    @classmethod
    def from_args(cls, args, token: str) -> "JenkinsProvider":
        return cls(job_url=args.url, user=args.user, token=token)

    @property
    def pipeline_name(self) -> str:
        return self.handler.job_name

    def trigger_and_wait(self, params: dict) -> dict:
        raw = self.handler.trigger_pipeline(params=params, wait=True)
        status = raw.get("status")
        return {
            "provider": self.name,
            "pipeline_name": self.pipeline_name,
            "build_id": raw["build_number"],
            "status": status,
            "succeeded": status == "SUCCESS",
            "duration_s": raw.get("duration_s"),
            "params_used": raw.get("params_used", {}),
            "url": raw.get("console_url"),
            "tests": raw.get("tests"),
            # Kept for existing callers (qa-verify-cve-fix, run_qa_verdict).
            "job_url": raw.get("job_url"),
            "build_number": raw["build_number"],
            "console_url": raw.get("console_url"),
        }

    def collect_artifacts(
        self,
        build_id,
        dest_dir: str,
        extensions: Optional[tuple[str, ...]] = None,
    ) -> tuple[list[dict], list[str]]:
        artifacts = self.handler.list_artifacts(build_id)
        downloaded = self.handler.download_artifacts(build_id, dest_dir, extensions)
        return artifacts, downloaded
