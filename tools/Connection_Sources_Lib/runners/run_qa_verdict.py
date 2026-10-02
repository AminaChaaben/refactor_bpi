import argparse
import json
import sys


from Talan_Library.qa_executor_Lib.qa_executor import QAExecutor
from Talan_Library.Connection_Sources_Lib.schema import PolicyPayload, Project, Vulnerability, Policy


def main():
    parser = argparse.ArgumentParser(
        description="Persist a QA verdict (and optionally a Jenkins pipeline result) "
                    "for the latest attempt, then write output-<project_id>-<cve_id>.json"
    )
    parser.add_argument("--domain", required=True, help="e.g. https://gitlab.com")
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--mr-iid", required=True, type=int)
    parser.add_argument("--cve-id", required=True, help="e.g. CVE-2023-38545")
    parser.add_argument("--severity", required=True, help="e.g. Critical")
    parser.add_argument("--max-retries", required=True, type=int)
    parser.add_argument("--max-budget", required=True, type=float)
    parser.add_argument("--consumed-budget", required=True, type=float)
    parser.add_argument("--verdict", required=True, choices=["pass", "fail"],
                        help="pass -> verdict_qa true, fail -> verdict_qa false")
    parser.add_argument("--feedback", required=True, help="Feedback text recorded as feedback_qa")
    parser.add_argument("--pipeline-result-file", default=None,
                        help="Path to the JSON file produced by run_pipeline; recorded on the "
                             "latest attempt before the verdict is written")
    args = parser.parse_args()

    payload = PolicyPayload(
        project=Project(
            domain=args.domain,
            project_id=args.project_id,
            mr_iid=args.mr_iid,
        ),
        vulnerability=Vulnerability(cve_id=args.cve_id, severity=args.severity),
        policy=Policy(max_retries=args.max_retries, max_budget=args.max_budget),
        consumed_budget=args.consumed_budget,
    )
    executor = QAExecutor(payload)

    try:
        if args.pipeline_result_file:
            # utf-8-sig so a BOM-prefixed file still parses.
            with open(args.pipeline_result_file, encoding="utf-8-sig") as f:
                pipeline_result = json.load(f)
            executor.record_pipeline_result(pipeline_result)

        executor.update_qa_verdict(args.verdict == "pass", args.feedback)

        vuln_entry = executor.get_vulnerability_entry() or {}
        output_path = executor.get_output_path()
        with open(output_path, "w") as f:
            json.dump(vuln_entry, f, indent=2)
    except (FileNotFoundError, LookupError, ValueError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Deliberately terse: callers must not need the entry contents echoed back.
    print(f"verdict_qa={args.verdict} written to {output_path}")


if __name__ == "__main__":
    main()
