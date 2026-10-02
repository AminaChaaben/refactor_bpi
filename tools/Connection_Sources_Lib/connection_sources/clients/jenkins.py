"""Jenkins over its own REST/crumb API -- CI job creation, triggering, and results.

Jenkins is not an issue tracker: there is no "issue" to search/get/transition the way
Jira or GitLab have one. The abstract AlmClient CRUD methods are mapped onto the
closest Jenkins concept (a job is the created/updated/deleted thing; a build is what
`transition` cannot mean, so it raises) and the real work -- triggering a build,
polling it to a terminal result, reading its console log -- lives in the extra
methods below, the same way GitLab's pipeline-specific methods sit alongside its
abstract CRUD in clients/gitlab.py.

Auth is HTTP Basic with a username + API token (Jenkins' "My Account -> API Token"),
via the same `basic_auth()` helper every other client here uses. A real (non-throwaway)
Jenkins normally has CSRF protection (a "crumb") enabled on every state-changing
request; `_crumb_headers()` fetches one lazily and sends it automatically. A crumb
issuer that is absent or disabled (as our own docker-compose.jenkins.yml dev instance
deliberately configures, via basic-security.groovy's `setCrumbIssuer(null)`) degrades
silently to no crumb header, not an error -- so this same client works unmodified
against a real Jenkins with CSRF on.
"""

from __future__ import annotations

import time
from typing import Any, Mapping
from xml.sax.saxutils import escape as _xml_escape

import httpx

from ..errors import ApiError, NotFoundError, SourcesConfigError
from ..models import AlmRecord, Identity
from .base import DEFAULT_TIMEOUT, MAX_PAGE, AlmClient, ClientSpec, basic_auth

__all__ = ["JenkinsClient", "freestyle_shell_job_xml", "pipeline_scm_job_xml"]

# A build can sit QUEUED for a moment before Jenkins assigns it a real number, and
# then run for a while -- both are polled, not pushed, since Jenkins has no webhook
# without a plugin this dev instance does not have installed.
_BUILD_TERMINAL = frozenset({"SUCCESS", "FAILURE", "ABORTED", "UNSTABLE", "NOT_BUILT"})


class JenkinsClient(AlmClient):
    spec = ClientSpec(
        name="jenkins",
        required_env=("JENKINS_URL", "JENKINS_USER", "JENKINS_TOKEN"),
        token_hint=(
            "create an API token at <jenkins>/user/<you>/security/ (\"Add new "
            "Token\") -- the account password itself is not accepted by the API"
        ),
    )

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "JenkinsClient":
        return cls(
            base_url=env["JENKINS_URL"],
            auth=basic_auth(env["JENKINS_USER"], env["JENKINS_TOKEN"]),
            env=env,
            timeout=timeout,
            transport=transport,
        )

    def ping(self) -> Identity:
        info = self.request("GET", "/api/json")
        return Identity(
            system="jenkins",
            account=self._env.get("JENKINS_USER") or "unknown",
            base_url=self.base_url,
        )

    # -- CSRF crumb ----------------------------------------------------------

    def _crumb_headers(self) -> dict[str, str]:
        """A fresh crumb header for one state-changing call, or {} when this
        Jenkins has no crumb issuer enabled. Never cached: a crumb is invalidated
        by the session it was issued for expiring, and re-fetching costs one cheap
        GET against a call that is itself already a real network round trip.
        """
        try:
            crumb = self.request("GET", "/crumbIssuer/api/json")
        except NotFoundError:
            return {}
        field = crumb.get("crumbRequestField")
        value = crumb.get("crumb")
        if not field or not value:
            return {}
        return {field: value}

    # -- jobs ------------------------------------------------------------

    def job_exists(self, name: str) -> bool:
        try:
            self.request("GET", f"/job/{name}/api/json")
            return True
        except NotFoundError:
            return False

    def create_job(self, name: str, config_xml: str) -> dict[str, Any]:
        self.request(
            "POST",
            "/createItem",
            params={"name": name},
            content=config_xml,
            content_type="application/xml",
            headers=self._crumb_headers(),
            raw=True,
            follow_redirects=True,
        )
        return {"name": name, "action": "created", "url": f"{self.base_url}/job/{name}/"}

    def update_job_config(self, name: str, config_xml: str) -> dict[str, Any]:
        self.request(
            "POST",
            f"/job/{name}/config.xml",
            content=config_xml,
            content_type="application/xml",
            headers=self._crumb_headers(),
            raw=True,
        )
        return {"name": name, "action": "updated", "url": f"{self.base_url}/job/{name}/"}

    def publish_job(self, name: str, config_xml: str) -> dict[str, Any]:
        """Create the job if it does not exist yet, else update its config in place.

        Idempotent by design: PUBLISH_CI may be approved and re-run (a failed
        Jenkins-side publish leaves the pending action untouched, same as
        approve-push), and a second attempt must not fail with "already exists".
        """
        if self.job_exists(name):
            return self.update_job_config(name, config_xml)
        return self.create_job(name, config_xml)

    def delete_job(self, name: str) -> dict[str, Any]:
        # Jenkins answers a successful doDelete with a 302 to the job list, not a
        # 200 -- the same "form action succeeded, here's where to look next"
        # pattern createItem uses. Without follow_redirects, the shared _checked()
        # in base.py cannot tell that redirect apart from an auth failure and
        # raises CredentialError on a delete that actually succeeded (seen live).
        self.request(
            "POST",
            f"/job/{name}/doDelete",
            headers=self._crumb_headers(),
            raw=True,
            follow_redirects=True,
        )
        return {
            "system": "jenkins",
            "id": name,
            "key": name,
            "title": name,
            "deleted": True,
            "recoverable": False,
        }

    # -- builds ------------------------------------------------------------

    def trigger_build(
        self, name: str, *, parameters: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """Queue one build. Jenkins answers 201 with a `Location` header pointing at
        a *queue item*, not a build -- a build only exists once the queue actually
        schedules it onto an executor, which is why this returns a queue URL for
        `wait_for_queued_build` to resolve into a real build number."""
        path = f"/job/{name}/buildWithParameters" if parameters else f"/job/{name}/build"
        response = self.request(
            "POST",
            path,
            params=dict(parameters) if parameters else None,
            headers=self._crumb_headers(),
            raw=True,
            follow_redirects=False,
        )
        location = response.headers.get("Location", "")
        if not location:
            raise ApiError(
                f"jenkins: build trigger for {name!r} returned no queue Location",
                server="jenkins",
                remediation="check the job exists and is not disabled",
            )
        return {"job_name": name, "queue_url": _relative(self.base_url, location)}

    def queue_item(self, queue_url: str) -> dict[str, Any]:
        return self.request("GET", f"{queue_url.rstrip('/')}/api/json")

    # -- plugins -------------------------------------------------------------
    # A genuine Jenkinsfile-backed SCM Pipeline job (pipeline_scm_job_xml above)
    # needs the `git` and `workflow-aggregator` (Pipeline) plugins, neither of
    # which ship in the bare jenkins/jenkins:lts-jdk17 image. These three methods
    # are one-time instance provisioning, not per-build runtime calls -- the CLI
    # exposes them as `jenkins-plugins-install`/`jenkins-plugins-list`.

    def installed_plugins(self) -> dict[str, dict[str, Any]]:
        payload = self.request(
            "GET", "/pluginManager/api/json", params={"depth": 1}
        )
        return {p["shortName"]: p for p in payload.get("plugins", []) if "shortName" in p}

    def wait_for_plugin_installs(self, *, timeout: float = 300.0, poll: float = 3.0) -> None:
        """Block until every queued plugin install job (and the full dependency
        tree Jenkins queues alongside it -- installing `workflow-aggregator`
        alone queued 46 jobs, live) reaches a terminal state.

        Necessary before `restart()`: restarting while jobs are still Pending
        only activates whichever ones happened to finish first, silently
        dropping the rest -- seen live, install_plugins(["git",
        "workflow-aggregator"]) followed by a fixed 20s sleep restarted with
        only 5 of 46 dependency jobs done, and neither target plugin installed.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self.request(
                "GET", "/updateCenter/api/json", params={"tree": "jobs[*]"}
            )
            install_jobs = [
                job for job in payload.get("jobs", []) if job.get("type") == "InstallationJob"
            ]
            pending = [
                job for job in install_jobs
                if not str(job.get("status", {}).get("_class", "")).endswith(("Success", "Fail"))
            ]
            if install_jobs and not pending:
                failed = [
                    job["name"] for job in install_jobs
                    if str(job.get("status", {}).get("_class", "")).endswith("Fail")
                ]
                if failed:
                    raise ApiError(
                        f"jenkins: plugin install failed for: {', '.join(failed)}",
                        server="jenkins",
                        remediation="check Manage Jenkins -> Plugins -> Installed for details",
                    )
                return
            time.sleep(poll)
        raise ApiError(
            f"jenkins: plugin installs still pending after {timeout}s",
            server="jenkins",
            remediation="raise --timeout, or check network access to updates.jenkins.io",
            retryable=True,
        )

    def install_plugins(self, plugin_ids: list[str]) -> dict[str, Any]:
        """Queue installation of the given plugin short names (e.g. "git",
        "workflow-aggregator"). Installation happens in the background against
        Jenkins' own update center; the plugin is only fully active after
        `restart()` -- call `wait_until_up()` afterwards and check
        `installed_plugins()` to confirm.
        """
        body = "<jenkins>" + "".join(
            f'<install plugin="{_xml_escape(pid)}@latest" />' for pid in plugin_ids
        ) + "</jenkins>"
        self.request(
            "POST",
            "/pluginManager/installNecessaryPlugins",
            content=body,
            content_type="text/xml",
            headers=self._crumb_headers(),
            raw=True,
            follow_redirects=True,
        )
        return {"requested": plugin_ids}

    def restart(self, *, safe: bool = True) -> None:
        """Restart Jenkins to activate newly-installed plugins. `safe` waits for
        any running builds to finish first; use it unless this is a throwaway
        dev instance known to be idle."""
        path = "/safeRestart" if safe else "/restart"
        try:
            self.request("POST", path, headers=self._crumb_headers(), raw=True, follow_redirects=True)
        except (ApiError, httpx.HTTPError):
            # The connection is expected to drop mid-restart -- a network error
            # here is the restart working, not failing. wait_until_up() is the
            # real check of whether it comes back.
            pass

    def wait_until_up(self, *, timeout: float = 180.0, poll: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.request("GET", "/api/json")
                return
            except Exception as exc:  # noqa: BLE001 -- any failure just means "not up yet"
                last_error = exc
                time.sleep(poll)
        raise ApiError(
            f"jenkins: did not come back up within {timeout}s after restart",
            server="jenkins",
            remediation=f"check `docker logs` -- last error while polling was: {last_error}",
        )

    def wait_for_queued_build(
        self, queue_url: str, *, timeout: float = 60.0, poll: float = 2.0
    ) -> int:
        """Poll a queue item until Jenkins assigns it a real build number."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.queue_item(queue_url)
            if item.get("cancelled"):
                raise ApiError(
                    "jenkins: queued build was cancelled before it started",
                    server="jenkins",
                    remediation="check executor availability and job configuration",
                )
            executable = item.get("executable")
            if isinstance(executable, dict) and executable.get("number"):
                return int(executable["number"])
            time.sleep(poll)
        raise ApiError(
            f"jenkins: build still queued after {timeout}s (no executor picked it up)",
            server="jenkins",
            remediation="check Jenkins has a free executor (Manage Jenkins -> Nodes)",
            retryable=True,
        )

    def build_status(self, name: str, number: int) -> dict[str, Any]:
        return self.request("GET", f"/job/{name}/{number}/api/json")

    def build_console(self, name: str, number: int) -> str:
        response = self.request("GET", f"/job/{name}/{number}/consoleText", raw=True)
        return response.text

    def wait_for_build(
        self, name: str, number: int, *, timeout: float = 600.0, poll: float = 3.0
    ) -> tuple[str, bool]:
        """Poll a running build to a terminal result.

        Returns (result, timed_out) -- mirrors gitlab.py's `_wait_for_pipeline`
        shape so the CLI's --wait handling is identical between the two.
        """
        deadline = time.monotonic() + timeout
        result = "UNKNOWN"
        while time.monotonic() < deadline:
            detail = self.build_status(name, number)
            if not detail.get("building"):
                return str(detail.get("result") or "UNKNOWN"), False
            time.sleep(poll)
        return result, True

    # -- abstract CRUD: mapped onto jobs, not issues ------------------------

    def search(self, scope: Mapping[str, Any], *, limit: int = MAX_PAGE) -> list[AlmRecord]:
        payload = self.request(
            "GET", "/api/json", params={"tree": "jobs[name,url,color,lastBuild[number,result]]"}
        )
        jobs = payload.get("jobs") or []
        return [self._record(job) for job in jobs[:limit]]

    def get(self, ident: str) -> AlmRecord:
        job = self.request(
            "GET", f"/job/{ident}/api/json",
            params={"tree": "name,url,color,lastBuild[number,result]"},
        )
        return self._record(job)

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        name = fields.get("job_name") or scope.get("job_name")
        config_xml = fields.get("config_xml")
        if not name or not config_xml:
            raise SourcesConfigError(
                "jenkins create needs job_name and config_xml",
                server="jenkins",
                remediation="pass --job-name and --config-xml (a path to the job's config.xml)",
            )
        self.publish_job(str(name), str(config_xml))
        return self.get(str(name))

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        config_xml = fields.get("config_xml")
        if not config_xml:
            raise SourcesConfigError(
                "jenkins update needs config_xml",
                server="jenkins",
                remediation="pass --config-xml (a path to the job's new config.xml)",
            )
        self.update_job_config(ident, str(config_xml))
        return self.get(ident)

    def transition(self, ident: str, status: str) -> AlmRecord:
        raise SourcesConfigError(
            "jenkins jobs have no issue-tracker-style status to transition",
            server="jenkins",
            remediation="use trigger_build()/wait_for_build() to run and read a build's "
            "own SUCCESS/FAILURE/ABORTED result instead",
        )

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        """Jenkins has no recycle bin: `permanent` is accepted for signature parity
        and changes nothing, same as gitlab.py's delete()."""
        return self.delete_job(ident)

    def _record(self, job: Mapping[str, Any]) -> AlmRecord:
        last_build = job.get("lastBuild") if isinstance(job.get("lastBuild"), Mapping) else None
        status = str((last_build or {}).get("result") or _color_to_status(job.get("color")))
        name = str(job.get("name") or "")
        return AlmRecord(
            system="jenkins",
            id=name,
            key=name,
            title=name,
            status=status,
            type="JENKINS_JOB",
            url=str(job.get("url") or f"{self.base_url}/job/{name}/"),
            raw=dict(job),
        )


def _relative(base_url: str, location: str) -> str:
    """A `Location` header is always absolute; the caller re-enters it through this
    same client's `request()`, which merges paths against `base_url` -- so an
    absolute URL under a DIFFERENT host (a proxy rewrote it, say) would silently
    escape this client's auth and error handling. Stripping down to the path here
    makes that impossible instead of trusting the header blindly."""
    trimmed_base = base_url.rstrip("/")
    if location.startswith(trimmed_base):
        return location[len(trimmed_base):]
    # Not under our own base_url at all -- still return a same-origin-relative form
    # (from the first single "/" after the scheme+host) rather than the raw absolute
    # URL, so a caller can never accidentally point this client at another host.
    if "://" not in location:
        return location
    after_scheme = location.split("://", 1)[1]
    slash = after_scheme.find("/")
    return after_scheme[slash:] if slash != -1 else "/"


def _color_to_status(color: Any) -> str:
    """Jenkins' job-list `color` field is its only "last result" signal when a job
    has no build yet reachable via `lastBuild`: blue=SUCCESS, red=FAILURE,
    yellow=UNSTABLE, grey/notbuilt=NOT_BUILT, disabled=DISABLED. An `_anime` suffix
    (e.g. "blue_anime") means a build is currently running -- stripped before
    mapping so an in-progress success-streak job does not read as unknown."""
    text = str(color or "").removesuffix("_anime")
    return {
        "blue": "SUCCESS",
        "green": "SUCCESS",
        "red": "FAILURE",
        "yellow": "UNSTABLE",
        "grey": "NOT_BUILT",
        "notbuilt": "NOT_BUILT",
        "disabled": "DISABLED",
        "aborted": "ABORTED",
    }.get(text, "UNKNOWN")


def freestyle_shell_job_xml(*, description: str, shell_command: str) -> str:
    """A minimal FreeStyleProject + a single 'Execute shell' build step.

    Uses only classes built into Jenkins core (`hudson.model.FreeStyleProject`,
    `hudson.tasks.Shell`) -- no plugin has to be installed for this job type to
    exist, unlike a Pipeline (`workflow-job`/`workflow-cps`) job.
    """
    return (
        "<?xml version='1.1' encoding='UTF-8'?>\n"
        "<project>\n"
        f"  <description>{_xml_escape(description)}</description>\n"
        "  <keepDependencies>false</keepDependencies>\n"
        "  <properties/>\n"
        '  <scm class="hudson.scm.NullSCM"/>\n'
        "  <canRoam>true</canRoam>\n"
        "  <disabled>false</disabled>\n"
        "  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>\n"
        "  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>\n"
        "  <triggers/>\n"
        "  <concurrentBuild>false</concurrentBuild>\n"
        "  <builders>\n"
        "    <hudson.tasks.Shell>\n"
        f"      <command>{_xml_escape(shell_command)}</command>\n"
        "    </hudson.tasks.Shell>\n"
        "  </builders>\n"
        "  <publishers/>\n"
        "  <buildWrappers/>\n"
        "</project>\n"
    )


def pipeline_scm_job_xml(
    *, description: str, repo_url: str, branch: str, script_path: str = "Jenkinsfile",
    credentials_id: str = "",
) -> str:
    """A Pipeline job (`WorkflowJob`) whose script is read from a Jenkinsfile already
    committed to the given git ref, via `CpsScmFlowDefinition` -- the SCM-backed form,
    not an inline script, so the pipeline is versioned with the code it tests.

    Needs the `workflow-job`, `workflow-cps`, `workflow-scm-step` and `git` plugins
    installed (none are in the bare jenkins/jenkins:lts-jdk17 image by default) --
    see docker/README or the plugin-install step this project's setup runs once
    against the local dev Jenkins.
    """
    credentials_xml = (
        f"<credentialsId>{_xml_escape(credentials_id)}</credentialsId>" if credentials_id else ""
    )
    return (
        "<?xml version='1.1' encoding='UTF-8'?>\n"
        '<flow-definition plugin="workflow-job">\n'
        f"  <description>{_xml_escape(description)}</description>\n"
        "  <keepDependencies>false</keepDependencies>\n"
        "  <properties/>\n"
        '  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">\n'
        '    <scm class="hudson.plugins.git.GitSCM" plugin="git">\n'
        "      <configVersion>2</configVersion>\n"
        "      <userRemoteConfigs>\n"
        "        <hudson.plugins.git.UserRemoteConfig>\n"
        f"          <url>{_xml_escape(repo_url)}</url>\n"
        f"          {credentials_xml}\n"
        "        </hudson.plugins.git.UserRemoteConfig>\n"
        "      </userRemoteConfigs>\n"
        "      <branches>\n"
        "        <hudson.plugins.git.BranchSpec>\n"
        f"          <name>{_xml_escape(branch)}</name>\n"
        "        </hudson.plugins.git.BranchSpec>\n"
        "      </branches>\n"
        "      <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>\n"
        "      <submoduleCfg class=\"empty-list\"/>\n"
        "      <extensions/>\n"
        "    </scm>\n"
        f"    <scriptPath>{_xml_escape(script_path)}</scriptPath>\n"
        "    <lightweight>true</lightweight>\n"
        "  </definition>\n"
        "  <triggers/>\n"
        "  <disabled>false</disabled>\n"
        "</flow-definition>\n"
    )
