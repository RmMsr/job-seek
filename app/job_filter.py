from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping
from urllib.parse import urlencode

VALID_TABS = ("new", "lead", "accepted", "pending", "rejected", "not_relevant", "trash")
VALID_ORDERS = ("change", "score", "age")


@dataclass(frozen=True)
class JobFilter:
    statuses: tuple[str, ...] = ("new",)
    scenario_id: int | None = None
    scenario_none: bool = False
    source_id: int | None = None
    org: str | None = None
    org_none: bool = False
    order: str = "change"
    q: str = ""

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> "JobFilter":
        get = params.get
        raw_status = get("status") or ""
        picked: list[str] = []
        for part in raw_status.split(","):
            part = part.strip()
            if part in VALID_TABS and part not in picked:
                picked.append(part)
        statuses = tuple(picked) or ("new",)

        raw_scenario = (get("scenario") or get("scenario_id") or "").strip()
        scenario_id: int | None = None
        scenario_none = False
        if raw_scenario == "none":
            scenario_none = True
        elif raw_scenario.isdigit():
            scenario_id = int(raw_scenario)

        raw_source = (get("source_id") or "").strip()
        source_id = int(raw_source) if raw_source.isdigit() else None

        raw_org = (get("org") or "").strip()
        org_none = raw_org == "none"
        org = None if org_none else (raw_org or None)

        order = (get("order") or "").strip()
        if order not in VALID_ORDERS:
            order = "change"

        query = (get("q") or "").strip()

        return cls(statuses, scenario_id, scenario_none, source_id, org, org_none, order, query)

    @property
    def is_narrowed(self) -> bool:
        return bool(
            self.q
            or self.scenario_id is not None
            or self.scenario_none
            or self.source_id is not None
            or self.org is not None
            or self.org_none
        )

    @property
    def searching(self) -> bool:
        return bool(self.q)

    def query_params(self) -> dict[str, str]:
        out = {"status": ",".join(self.statuses)}
        if self.scenario_none:
            out["scenario"] = "none"
        elif self.scenario_id is not None:
            out["scenario"] = str(self.scenario_id)
        if self.source_id is not None:
            out["source_id"] = str(self.source_id)
        if self.org_none:
            out["org"] = "none"
        elif self.org is not None:
            out["org"] = self.org
        if self.order != "change":
            out["order"] = self.order
        if self.q:
            out["q"] = self.q
        return out

    def query_suffix(self, *, detail: bool = False) -> str:
        if detail:
            return "?detail=1"
        return "?" + urlencode(self.query_params())

    def cleared(self) -> "JobFilter":
        """Drop the query and every scenario/source/org narrowing; keep statuses + order."""
        return JobFilter(statuses=self.statuses, order=self.order)

    @property
    def status_tab(self) -> str:
        return self.statuses[0]

    @property
    def is_multi(self) -> bool:
        return len(self.statuses) > 1

    def for_status(self, tab: str) -> "JobFilter":
        return replace(self, statuses=(tab,))

    def with_statuses(self, statuses) -> "JobFilter":
        picked = tuple(s for s in VALID_TABS if s in set(statuses)) or ("new",)
        return replace(self, statuses=picked)

    def with_status_toggled(self, tab: str) -> "JobFilter":
        if tab in self.statuses:
            remaining = tuple(s for s in self.statuses if s != tab)
            return replace(self, statuses=remaining or self.statuses)
        return replace(self, statuses=tuple(s for s in VALID_TABS if s in self.statuses or s == tab))

    def with_scenario_id(self, sid) -> "JobFilter":
        return replace(self, scenario_id=int(sid), scenario_none=False)

    def with_source_id(self, sid) -> "JobFilter":
        return replace(self, source_id=int(sid))

    def with_org(self, org: str) -> "JobFilter":
        return replace(self, org=org)
