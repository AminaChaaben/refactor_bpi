<!-- Powered by BMAD-CORE™ -->

# Export Tickets

---

## Overview

Converts the top N ranked opportunities from a named, completed `rrd-audit-all` run into GitLab issues via the GitLab API/`glab`/`gh`-equivalent tooling available in the environment. Creates no new findings, runs no detectors — purely a format conversion plus a write to an external, team-shared system, which is why it carries the strictest consent requirement of any Refactor Radar workflow (see `SKILL.md`'s Explicit Consent Required section).

---

## INITIALIZATION SEQUENCE

Load `{skill-root}/steps/step-01-export.md` directly.
