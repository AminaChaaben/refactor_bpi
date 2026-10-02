"""
CI/CD providers for run_pipeline.py. To add one (GitHub Actions, Azure
Pipelines, ...): subclass PipelineProvider, return the normalized result from
trigger_and_wait(), and register the class in PROVIDERS below. The runner and
the qa-trigger-pipeline skill steps don't change.
"""

from typing import Optional

from Talan_Library.Connection_Sources_Lib.pipeline_providers.base import PipelineProvider
from Talan_Library.Connection_Sources_Lib.pipeline_providers.gitlab import GitLabProvider
from Talan_Library.Connection_Sources_Lib.pipeline_providers.jenkins import JenkinsProvider

PROVIDERS: dict[str, type[PipelineProvider]] = {
    JenkinsProvider.name: JenkinsProvider,
    GitLabProvider.name: GitLabProvider,
}


def resolve_provider(name: Optional[str], url: Optional[str]) -> type[PipelineProvider]:
    """An explicit name wins; otherwise detect from the URL. Raises ValueError
    naming the supported providers when neither resolves."""
    supported = ", ".join(PROVIDERS)
    if name:
        cls = PROVIDERS.get(name.strip().lower())
        if cls is None:
            raise ValueError(f"provider '{name}' is not supported yet. Supported: {supported}")
        return cls
    matches = [cls for cls in PROVIDERS.values() if cls.detect(url)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(
        f"could not tell the provider from URL {url!r}; pass --provider ({supported})"
    )
