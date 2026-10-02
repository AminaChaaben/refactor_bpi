from abc import ABC, abstractmethod
from typing import Optional


class PipelineProvider(ABC):
    """
    One CI/CD system (Jenkins, GitLab CI, ...) behind the same interface, so
    run_pipeline.py never branches on the provider itself.

    A provider triggers one build, waits for it, and returns the *normalized
    result*: the same keys whatever the CI system, so the qa-trigger-pipeline
    skill and anything downstream read one shape:

        provider, pipeline_name, build_id, status, succeeded, duration_s,
        params_used, url, tests ({passed, failed, skipped, failing_cases} or None)

    The runner then adds artifacts, downloaded_files, run_dir and result_file.
    A provider may add its own extra keys (Jenkins keeps job_url, build_number
    and console_url for existing callers).
    """

    # Registry key, e.g. "jenkins". Also the value of --provider.
    name: str = ""
    # QA-specific token variable first, then the plain one.
    token_vars: tuple[str, ...] = ()
    # CLI arguments (argparse dest names) this provider can't run without.
    required_args: tuple[str, ...] = ()

    @staticmethod
    @abstractmethod
    def detect(url: str) -> bool:
        """True when `url` clearly belongs to this provider. Only a fallback:
        an explicit --provider always wins."""

    @classmethod
    @abstractmethod
    def from_args(cls, args, token: str) -> "PipelineProvider":
        """Build the provider from the parsed CLI arguments. The runner has
        already checked `required_args` and loaded the token."""

    @property
    @abstractmethod
    def pipeline_name(self) -> str:
        """Stable name for this pipeline, used for the output folder
        (pipelines/<pipeline_name>/build-<id>/). May contain "/"."""

    @abstractmethod
    def trigger_and_wait(self, params: dict) -> dict:
        """Trigger exactly one build with `params`, block until it finishes,
        and return the normalized result (without artifacts)."""

    @abstractmethod
    def collect_artifacts(
        self,
        build_id,
        dest_dir: str,
        extensions: Optional[tuple[str, ...]] = None,
    ) -> tuple[list[dict], list[str]]:
        """Download the build's artifacts under dest_dir, optionally filtered
        by suffix. Returns (artifacts as reported by the CI system, local
        paths written)."""
