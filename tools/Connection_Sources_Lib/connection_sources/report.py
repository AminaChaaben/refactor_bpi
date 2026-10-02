"""Build, write and read per-project connection reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ReportError
from .models import ConnectionReport, ConnectionResult

__all__ = ["ConnectionReportBuilder", "ReportError"]


class ConnectionReportBuilder:
    """Reusable builder: raw tool results -> ConnectionReport -> report.json."""

    def __init__(self, project: str) -> None:
        self.project = project

    @staticmethod
    def from_results(
        project: str,
        results: list[dict[str, Any]],
        checked_at: str,
    ) -> ConnectionReport:
        connections = tuple(
            ConnectionResult(
                source=str(result.get("source", "")),
                server=str(result.get("server", "")),
                ok=bool(result.get("ok", False)),
                item_count=int(result.get("item_count", 0)),
                error=result.get("error"),
                details=dict(result.get("details", {})),
            )
            for result in results
        )
        return ConnectionReport(
            project=project,
            checked_at=checked_at,
            connections=connections,
        )

    def write(self, report: ConnectionReport, out: str | Path) -> Path:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def read(path: str | Path) -> ConnectionReport:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return ConnectionReport(
            project=str(raw.get("project", "")),
            checked_at=str(raw.get("checked_at", "")),
            connections=tuple(
                ConnectionResult(
                    source=str(name),
                    server=str(value.get("server", "")),
                    ok=bool(value.get("ok", False)),
                    item_count=int(value.get("item_count", 0)),
                    error=value.get("error"),
                )
                for name, value in raw.get("connections", {}).items()
            ),
        )