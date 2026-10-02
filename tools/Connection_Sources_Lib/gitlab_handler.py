import requests
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 30  # seconds, per request


class GitLabHandler:
    """Handles all direct interactions with the GitLab API for a given merge request.

    This class is only responsible for talking to GitLab (fetching MR
    discussion comments). It knows nothing about CVEs, retries, or budget
    policy — that logic lives in QAExecutor.
    """

    def __init__(
        self,
        domain: str,
        project_id: int,
        token: str = None,
        mr_iid: int = None,
        timeout: float = DEFAULT_TIMEOUT,
        allow_insecure_http: bool = False,
    ):
        parsed = urlparse(domain)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"domain must be an absolute http(s) URL, got {domain!r}")
        if parsed.scheme == "http" and token and not allow_insecure_http:
            raise ValueError(
                "Refusing to send a GitLab token over plain http; use https "
                "or pass allow_insecure_http=True for a local test instance."
            )
        self.domain = domain.rstrip("/")
        self.project_id = project_id
        self.mr_iid = mr_iid
        self.token = token
        self.timeout = timeout

    def _headers(self):
        return {"PRIVATE-TOKEN": self.token} if self.token else {}

    def get_mr_comments(self, include_system_notes: bool = False):
        """Fetch all comments (discussions) from the merge request.

        Raises ValueError if no MR IID was given, LookupError if the
        project/MR doesn't exist, and PermissionError if authentication fails.
        """
        if self.mr_iid is None:
            raise ValueError("mr_iid is required to fetch merge request comments")

        base_url = (
            f"{self.domain}/api/v4/projects/{self.project_id}"
            f"/merge_requests/{self.mr_iid}/discussions"
        )

        comments = []
        page = 1

        while True:

            try:
                resp = requests.get(
                    base_url,
                    headers=self._headers(),
                    params={"per_page": 100, "page": page},
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as e:
                raise LookupError(f"Could not reach GitLab at {self.domain}: {e}")

            if resp.status_code == 404:
                raise LookupError(
                    f"Project {self.project_id} or MR !{self.mr_iid} not found on {self.domain}. "
                    "Check the domain, project ID and MR IID."
                )
            if resp.status_code in (401, 403):
                hint = (
                    "No token was supplied to GitLabHandler; pass the token you "
                    "resolved from your credential source."
                    if not self.token
                    else "Check that the token has access to this project."
                )
                raise PermissionError(
                    f"Authentication failed ({resp.status_code}) for {self.domain}. {hint}"
                )
            resp.raise_for_status()
            discussions = resp.json()

            if not discussions:
                break

            for discussion in discussions:
                for note in discussion["notes"]:
                    if not include_system_notes and note.get("system"):
                        continue
                    author = note.get("author") or {}
                    comments.append({
                        "author": author.get("username", "unknown"),
                        "body": note["body"],
                        "created_at": note["created_at"],
                        "resolved": note.get("resolved", False),
                        "thread_id": discussion["id"],
                    })

            total_pages = int(resp.headers.get("X-Total-Pages", 1))
            if page >= total_pages:
                break
            page += 1

        return comments
