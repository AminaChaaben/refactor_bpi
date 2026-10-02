"""Confluence as written context: which pages exist, who wrote them, what they name.

Read-only by construction. Every other client in this package can create, update,
transition and delete; this one raises on all four. That is a deliberate asymmetry,
not an unfinished job: the factory reads the wiki to understand what it was asked to
build, and an agent that can silently rewrite the specification it is being measured
against is a worse system than one that cannot. If writing to Confluence is ever
wanted, it should arrive as its own reviewed decision rather than as a method that
happened to be easy to add here.

Auth is the same Atlassian account as Jira -- same email, same API token, one site.
`from_env` fills the CONFLUENCE_* variables from the JIRA_* ones when they are
absent, because making someone paste the same token into a second pair of variables
is friction with no security benefit: it is literally the same credential, and two
copies of it only means one of them goes stale.

API choice: the v1 `/wiki/rest/api` content API rather than v2. v1 takes CQL (so a
project can scope by space, label, ancestor or date in one expression, exactly as the
Jira source takes JQL) and returns body, ancestors, labels, space and history in ONE
expanded response. v2 needs a separate call per page for labels and ancestors, which
on a wiki of any size is hundreds of round trips to learn the same thing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import httpx

from ..errors import SourcesConfigError, WriteBlockedError
from ..models import AlmRecord, Identity
from .base import DEFAULT_TIMEOUT, MAX_PAGE, AlmClient, ClientSpec, basic_auth

__all__ = ["ConfluenceClient"]

API = "/wiki/rest/api"

# Everything the graph extractor needs about a page, in one response. `body.storage`
# is the raw XHTML the editor saved -- the only place an inline Jira issue macro's
# key actually appears as text, which is what MENTIONS edges are recovered from.
EXPAND = (
    "space,version,history,history.createdBy,ancestors,metadata.labels,body.storage"
)
# Same, minus the body. A wiki page body is by far the largest part of the payload,
# so a project that only wants the page map can ask for the cheap shape.
EXPAND_NO_BODY = "space,version,history,history.createdBy,ancestors,metadata.labels"


def _wiki_base(url: str) -> str:
    """The site root, given either the site root or a Jira/Confluence URL under it.

    Accepts `https://acme.atlassian.net`, `.../wiki`, or a deep link someone pasted
    out of the browser, and always yields the site root -- API paths in this module
    carry their own `/wiki` prefix, so a base URL that already ends in `/wiki` would
    otherwise produce `/wiki/wiki/rest/api` and a 404 that looks like a permissions
    problem.
    """
    trimmed = (url or "").strip().rstrip("/")
    for suffix in ("/wiki", "/jira", "/browse"):
        if trimmed.lower().endswith(suffix):
            trimmed = trimmed[: -len(suffix)]
    return trimmed


class ConfluenceClient(AlmClient):
    """One Confluence Cloud site, read-only."""

    spec = ClientSpec(
        name="confluence",
        required_env=("CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"),
        scope_keys=("cql", "space_key"),
        token_hint=(
            "Confluence uses the same Atlassian API token as Jira: set CONFLUENCE_URL "
            "to the site root (https://<site>.atlassian.net) and leave "
            "CONFLUENCE_USERNAME/CONFLUENCE_API_TOKEN unset to reuse JIRA_USERNAME "
            "and JIRA_API_TOKEN"
        ),
    )

    #: CONFLUENCE_* variable -> the Jira variables it may borrow from, in order.
    #: Empty counts as absent: a variable left as `CONFLUENCE_URL=` in a .env is
    #: someone who has not filled it in, not someone asking for an empty base URL.
    _FALLBACKS = {
        "CONFLUENCE_URL": ("JIRA_URL", "JIRA_BASE_URL"),
        "CONFLUENCE_USERNAME": ("JIRA_USERNAME", "JIRA_EMAIL"),
        "CONFLUENCE_API_TOKEN": ("JIRA_API_TOKEN",),
    }

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> "ConfluenceClient":
        # Fill from Jira BEFORE the base class checks for missing credentials, so a
        # site configured only for Jira is not reported as missing three variables it
        # does not need. The fallback is one-directional and explicit: Confluence may
        # borrow Jira's credential, never the other way round.
        filled = dict(env)
        for target, candidates in cls._FALLBACKS.items():
            if filled.get(target):
                continue
            for candidate in candidates:
                if env.get(candidate):
                    filled[target] = env[candidate]
                    break
        return super().from_env(filled, timeout=timeout, transport=transport)  # type: ignore[return-value]

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "ConfluenceClient":
        return cls(
            base_url=_wiki_base(env["CONFLUENCE_URL"]),
            auth=basic_auth(env["CONFLUENCE_USERNAME"], env["CONFLUENCE_API_TOKEN"]),
            env=env,
            timeout=timeout,
            transport=transport,
        )

    # -- reads -------------------------------------------------------------

    def ping(self) -> Identity:
        me = self.request("GET", f"{API}/user/current")
        return Identity(
            system="confluence",
            account=me.get("email") or me.get("displayName") or me.get("accountId") or "unknown",
            base_url=self.base_url,
        )

    @staticmethod
    def cql_for(scope: Mapping[str, Any]) -> str:
        """The CQL this scope means.

        A project writes `space_key` in the ordinary case and `cql` when it needs
        something the simple form cannot say. Both are accepted; an explicit `cql`
        wins, because a project that wrote one meant it.
        """
        explicit = str(scope.get("cql") or "").strip()
        if explicit:
            return explicit
        space = str(scope.get("space_key") or "").strip()
        if not space:
            raise SourcesConfigError(
                "confluence source has neither 'space_key' nor 'cql' scope",
                server="confluence",
                remediation=(
                    "add \"space_key\": \"<KEY>\" to the confluence source in "
                    "sources.json, or a full \"cql\" expression"
                ),
            )
        # Quoted: space keys are usually bare words but nothing stops one from
        # containing a character CQL would parse.
        return f'space = "{space}" AND type = page'

    def iter_pages(
        self,
        scope: Mapping[str, Any],
        *,
        limit: int = MAX_PAGE,
        with_body: bool = True,
    ) -> Iterable[dict[str, Any]]:
        """Raw page payloads, paged, stopping at `limit`.

        Yields the tracker's own JSON rather than AlmRecord: the graph extractor
        needs ancestors, labels and the storage body, none of which survive the
        AlmRecord shape. `search` below is the AlmRecord-flavoured view for the
        generic CLI paths.
        """
        cql = self.cql_for(scope)
        expand = EXPAND if with_body else EXPAND_NO_BODY
        start = 0
        seen = 0
        while seen < limit:
            page_size = min(MAX_PAGE, limit - seen)
            payload = self.request(
                "GET",
                f"{API}/content/search",
                params={"cql": cql, "expand": expand, "start": start, "limit": page_size},
            )
            results = payload.get("results") if isinstance(payload, Mapping) else None
            if not isinstance(results, list) or not results:
                return
            for entry in results:
                if isinstance(entry, Mapping):
                    yield dict(entry)
                    seen += 1
                    if seen >= limit:
                        return
            # `_links.next` is the only reliable end-of-results signal: `size` is the
            # size of THIS page, and a full page is not proof another one exists.
            links = payload.get("_links") if isinstance(payload, Mapping) else None
            if not (isinstance(links, Mapping) and links.get("next")):
                return
            start += len(results)

    def search(
        self, scope: Mapping[str, Any], *, limit: int = MAX_PAGE
    ) -> list[AlmRecord]:
        return [self._record(page) for page in self.iter_pages(scope, limit=limit, with_body=False)]

    def get(self, ident: str) -> AlmRecord:
        page = self.request("GET", f"{API}/content/{ident}", params={"expand": EXPAND_NO_BODY})
        return self._record(page)

    def _record(self, page: Mapping[str, Any]) -> AlmRecord:
        space = page.get("space") if isinstance(page.get("space"), Mapping) else {}
        history = page.get("history") if isinstance(page.get("history"), Mapping) else {}
        created_by = history.get("createdBy") if isinstance(history.get("createdBy"), Mapping) else {}
        links = page.get("_links") if isinstance(page.get("_links"), Mapping) else {}
        webui = str(links.get("webui") or "")
        return AlmRecord(
            system="confluence",
            id=str(page.get("id") or ""),
            # The space key plus the page id: a page has no human key of its own, and
            # the title is not unique even within one space.
            key=f"{space.get('key') or 'WIKI'}-{page.get('id') or ''}",
            title=str(page.get("title") or ""),
            status=str(page.get("status") or "current"),
            type=str(page.get("type") or "page"),
            url=f"{self.base_url}/wiki{webui}" if webui else self.base_url,
            assignee=str(created_by.get("displayName") or "") or None,
            tags=[
                str(label.get("name"))
                for label in _labels_of(page)
                if isinstance(label, Mapping) and label.get("name")
            ],
            raw=dict(page),
        )

    # -- writes: refused, deliberately -------------------------------------

    def _refuse(self, action: str) -> "WriteBlockedError":
        return WriteBlockedError(
            f"confluence is a read-only source: {action} is not supported",
            server="confluence",
            remediation=(
                "the factory reads the wiki for context and never edits it -- make "
                "the change in Confluence yourself, or raise adding write support as "
                "its own decision"
            ),
        )

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        raise self._refuse("create")

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        raise self._refuse("update")

    def transition(self, ident: str, status: str) -> AlmRecord:
        raise self._refuse("transition")

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        raise self._refuse("delete")


def _labels_of(page: Mapping[str, Any]) -> list[Any]:
    metadata = page.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    labels = metadata.get("labels")
    if not isinstance(labels, Mapping):
        return []
    results = labels.get("results")
    return results if isinstance(results, list) else []
