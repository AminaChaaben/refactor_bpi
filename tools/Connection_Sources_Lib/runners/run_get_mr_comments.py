import argparse
import json
import os
import sys


from Talan_Library.qa_executor_Lib.config import settings
from Talan_Library.qa_executor_Lib.qa_executor import QAExecutor
from Talan_Library.Connection_Sources_Lib.schema import PolicyPayload, Project, Vulnerability, Policy


def main():
    parser = argparse.ArgumentParser(description="Fetch GitLab MR comments")
    parser.add_argument("--domain", required=True, help="e.g. https://gitlab.com")
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--mr-iid", required=True, type=int)
    parser.add_argument("--cve-id", required=True, help="e.g. CVE-2023-38545")
    parser.add_argument("--severity", required=True, help="e.g. Critical")
    parser.add_argument("--max-retries", required=True, type=int)
    parser.add_argument("--max-budget", required=True, type=float)
    parser.add_argument("--consumed-budget", required=True, type=float)
    parser.add_argument("--include-system-notes", action="store_true")
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

    token = settings.get_token("QA_GITLAB_TOKEN", "GITLAB_TOKEN")
    if not token:
        print("Error: neither QA_GITLAB_TOKEN nor GITLAB_TOKEN is set", file=sys.stderr)
        sys.exit(1)

    vuln_entry, error = executor.run_get_mr_comments(
        token=token,
        include_system_notes=args.include_system_notes,
    )
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(vuln_entry, indent=2))


if __name__ == "__main__":
    main()