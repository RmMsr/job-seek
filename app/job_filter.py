from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping
from urllib.parse import urlencode

VALID_TABS = ("new", "lead", "accepted", "rejected", "not_relevant", "trash")


@dataclass(frozen=True)
class JobFilter:
    status_tab: str = "new"
    scenario_id: int | None = None
    scenario_none: bool = False
    source_id: int | None = None
    org: str | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> "JobFilter":
        get = params.get
        tab = get("status") or "new"
        if tab not in VALID_TABS:
            tab = "new"

        raw_scenario = (get("scenario") or get("scenario_id") or "").strip()
        scenario_id: int | None = None
        scenario_none = False
        if raw_scenario == "none":
            scenario_none = True
        elif raw_scenario.isdigit():
            scenario_id = int(raw_scenario)

        raw_source = (get("source_id") or "").strip()
        source_id = int(raw_source) if raw_source.isdigit() else None

        org = (get("org") or "").strip() or None

        return cls(tab, scenario_id, scenario_none, source_id, org)

    @property
    def is_narrowed(self) -> bool:
        return bool(
            self.scenario_id is not None
            or self.scenario_none
            or self.source_id is not None
            or self.org is not None
        )

    def query_params(self) -> dict[str, str]:
        out = {"status": self.status_tab}
        if self.scenario_none:
            out["scenario"] = "none"
        elif self.scenario_id is not None:
            out["scenario"] = str(self.scenario_id)
        if self.source_id is not None:
            out["source_id"] = str(self.source_id)
        if self.org is not None:
            out["org"] = self.org
        return out

    def query_suffix(self, *, detail: bool = False) -> str:
        if detail:
            return "?detail=1"
        return "?" + urlencode(self.query_params())

    def cleared(self) -> "JobFilter":
        return JobFilter(status_tab=self.status_tab)

    def for_status(self, tab: str) -> "JobFilter":
        return replace(self, status_tab=tab)

    def with_scenario_id(self, sid) -> "JobFilter":
        return replace(self, scenario_id=int(sid), scenario_none=False)

    def with_source_id(self, sid) -> "JobFilter":
        return replace(self, source_id=int(sid))

    def with_org(self, org: str) -> "JobFilter":
        return replace(self, org=org)
