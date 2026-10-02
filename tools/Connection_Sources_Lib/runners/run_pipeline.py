import argparse
import json
import os
import shutil
import sys


from Talan_Library.Connection_Sources_Lib.pipeline_providers import PROVIDERS, resolve_provider
from Talan_Library.qa_executor_Lib.config import settings


def main():
    parser = argparse.ArgumentParser(
        description="Trigger one CI/CD pipeline build, wait for it, and collect its artifacts "
                    f"(providers: {', '.join(PROVIDERS)})"
    )
    parser.add_argument("--provider", default=None,
                        help=f"CI system: {', '.join(PROVIDERS)}. Omit to detect it from --url "
                             "(a Jenkins URL contains /job/)")
    parser.add_argument("--url", "--job-url", dest="url", required=True,
                        help="jenkins: job URL, e.g. http://localhost:8080/job/regression-suite/; "
                             "gitlab: host or project URL, e.g. https://gitlab.example.com/group/app")
    parser.add_argument("--user", default=None, help="jenkins: username that owns the token, e.g. ci-bot")
    parser.add_argument("--project", default=None,
                        help="gitlab: project id or group/path (overrides the path in --url)")
    parser.add_argument("--ref", default=None, help="gitlab: branch or tag to run the pipeline on")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="Pipeline parameter, repeatable, e.g. --param MR_ID=15 --param GIT_REF=develop "
                              "(jenkins: build parameters; gitlab: CI variables)")
    parser.add_argument("--extensions", default=None,
                         help="Comma-separated list of file extensions to collect, e.g. .xml,.html,.json (omit to grab everything)")
    parser.add_argument("--out", default=None,
                         help="Also write the result JSON to this path (it is always written to "
                              "pipelines/<pipeline>/build-<n>/pipeline-result.json under the QA executor "
                              "home), e.g. for run_qa_verdict --pipeline-result-file, without shell "
                              "redirection (which mangles the encoding on PowerShell)")
    args = parser.parse_args()

    try:
        provider_cls = resolve_provider(args.provider, args.url)
    except ValueError as e:
        fail(e)

    missing = [f"--{name}" for name in provider_cls.required_args if not getattr(args, name, None)]
    if missing:
        fail(f"provider '{provider_cls.name}' needs {', '.join(missing)}")

    # Parse --param KEY=VALUE pairs into a dict
    params = {}
    for item in args.param:
        if "=" not in item:
            fail(f"invalid --param '{item}', expected KEY=VALUE")
        key, value = item.split("=", 1)
        params[key] = value

    extensions = tuple(e.strip() for e in args.extensions.split(",")) if args.extensions else None

    token = settings.get_token(*provider_cls.token_vars)
    if not token:
        fail(f"neither {' nor '.join(provider_cls.token_vars)} is set")

    try:
        provider = provider_cls.from_args(args, token)
        # Trigger and wait first: the build id decides where this run's
        # artifacts and result land (pipelines/<pipeline>/build-<n>/).
        result = provider.trigger_and_wait(params)
        run_dir = settings.pipeline_run_dir(result["pipeline_name"], result["build_id"])
        result["artifacts"], result["downloaded_files"] = provider.collect_artifacts(
            result["build_id"], os.path.join(run_dir, "artifacts"), extensions
        )
    except Exception as e:
        fail(e)

    result["run_dir"] = run_dir
    result["result_file"] = os.path.join(run_dir, "pipeline-result.json")

    # Always keep the per-build copy; --out adds a copy at a caller-chosen path.
    for path in filter(None, (result["result_file"], args.out)):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

    prune_old_runs(os.path.dirname(run_dir), settings.keep_pipeline_runs)

    print(json.dumps(result, indent=2))


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def prune_old_runs(job_dir, keep):
    """Delete all but the `keep` highest-numbered build-<n> folders in job_dir."""
    builds = []
    for name in os.listdir(job_dir):
        number = name[len("build-"):]
        if name.startswith("build-") and number.isdigit():
            builds.append((int(number), os.path.join(job_dir, name)))
    for _, path in sorted(builds, reverse=True)[keep:]:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    main()
