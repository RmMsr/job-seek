from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping
from urllib.parse import urlencode

from app.tags import normalize_tag_name

VALID_TABS = ("new", "lead", "accepted", "pending", "rejected", "not_relevant", "trash")
VALID_ORDERS = ("change", "score", "age", "closing")


def _param_list(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return list(params.getlist(key))
    v = params.get(key)
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v] if v else []


def _unique_names(raw: list[str], skip: tuple[str, ...] = ()) -> tuple[str, ...]:
    seen = {s.casefold() for s in skip}
    out: list[str] = []
    for r in raw:
        name = normalize_tag_name(r)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return tuple(out)


def _drop(names: tuple[str, ...], name: str) -> tuple[str, ...]:
    return tuple(n for n in names if n.casefold() != name.casefold())


@dataclass(frozen=True)
class JobFilter:
    statuses: tuple[str, ...] = ("new",)
    scenario_id: int | None = None
    scenario_none: bool = False
    source_id: int | None = None
    org: str | None = None
    org_none: bool = False
    # The explicitly picked sort order, or None to use the tab's default (see `order`).
    order_pick: str | None = None
    q: str = ""
    # Set by an explicit single-status pick (a tab/More-panel link, the mobile
    # status <select>, or the Combine form's Apply) -- distinguishes "the
    # user chose exactly this one status" from "statuses defaulted to a
    # single tab because nothing else was specified". Search seeding
    # (app/routes/jobs.py's _effective_tabs) widens an untouched single
    # status to the multi-tab search scope, but must never override an
    # explicit pick back to that combo.
    solo: bool = False
    # Free manual tags: every name in `tags` must be on the job, none in
    # `not_tags` may be. Compared case-insensitively.
    tags: tuple[str, ...] = ()
    not_tags: tuple[str, ...] = ()

    @classmethod
    def from_params(cls, params: Mapping) -> "JobFilter":
        get = params.get
        # `status` may repeat (the Combine form's checkboxes) and/or be
        # comma-joined (links built from query_params()); merge them all.
        picked: list[str] = []
        for raw_status in _param_list(params, "status"):
            for part in (raw_status or "").split(","):
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
        order_pick = order if order in VALID_ORDERS else None

        query = (get("q") or "").strip()

        solo = (get("solo") or "").strip() == "1"

        tags = _unique_names(_param_list(params, "tag"))
        not_tags = _unique_names(_param_list(params, "not_tag"), skip=tags)

        return cls(
            statuses, scenario_id, scenario_none, source_id, org, org_none, order_pick, query, solo,
            tags, not_tags,
        )

    @property
    def order(self) -> str:
        """Effective sort order: the explicit pick, else accepted jobs by fit
        score and everything else by newest change."""
        if self.order_pick is not None:
            return self.order_pick
        return "score" if self.statuses == ("accepted",) else "change"

    @property
    def is_narrowed(self) -> bool:
        return bool(
            self.q
            or self.scenario_id is not None
            or self.scenario_none
            or self.source_id is not None
            or self.org is not None
            or self.org_none
            or self.tags
            or self.not_tags
        )

    @property
    def searching(self) -> bool:
        return bool(self.q)

    def query_params(self) -> dict[str, str | list[str]]:
        out: dict[str, str | list[str]] = {"status": ",".join(self.statuses)}
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
        if self.order_pick is not None:
            out["order"] = self.order_pick
        if self.q:
            out["q"] = self.q
        if self.solo:
            out["solo"] = "1"
        if self.tags:
            out["tag"] = list(self.tags)
        if self.not_tags:
            out["not_tag"] = list(self.not_tags)
        return out

    def query_string(self) -> str:
        return urlencode(self.query_params(), doseq=True)

    def query_suffix(self, *, detail: bool = False) -> str:
        if detail:
            return "?detail=1"
        return "?" + self.query_string()

    def cleared(self) -> "JobFilter":
        """Drop the query and every scenario/source/org/tag narrowing; keep statuses + order."""
        return JobFilter(statuses=self.statuses, order_pick=self.order_pick)

    @property
    def status_tab(self) -> str:
        return self.statuses[0]

    @property
    def is_multi(self) -> bool:
        return len(self.statuses) > 1

    def for_status(self, tab: str) -> "JobFilter":
        return replace(self, statuses=(tab,), solo=True)

    def with_statuses(self, statuses) -> "JobFilter":
        picked = tuple(s for s in VALID_TABS if s in set(statuses)) or ("new",)
        return replace(self, statuses=picked)

    def with_status_toggled(self, tab: str) -> "JobFilter":
        if tab in self.statuses:
            remaining = tuple(s for s in self.statuses if s != tab)
            return replace(self, statuses=remaining or self.statuses, solo=True)
        return replace(
            self, statuses=tuple(s for s in VALID_TABS if s in self.statuses or s == tab), solo=True
        )

    def with_scenario_id(self, sid) -> "JobFilter":
        return replace(self, scenario_id=int(sid), scenario_none=False)

    def with_source_id(self, sid) -> "JobFilter":
        return replace(self, source_id=int(sid))

    def with_org(self, org: str) -> "JobFilter":
        return replace(self, org=org)

    def with_tag(self, name: str) -> "JobFilter":
        name = normalize_tag_name(name)
        if not name or any(t.casefold() == name.casefold() for t in self.tags):
            return self
        return replace(self, tags=(*self.tags, name), not_tags=_drop(self.not_tags, name))

    def with_not_tag(self, name: str) -> "JobFilter":
        name = normalize_tag_name(name)
        if not name or any(t.casefold() == name.casefold() for t in self.not_tags):
            return self
        return replace(self, not_tags=(*self.not_tags, name), tags=_drop(self.tags, name))

    def with_tag_flipped(self, name: str) -> "JobFilter":
        # Flip using the stored spelling, so "REMOTE" flips "Remote" as-is.
        for t in self.tags:
            if t.casefold() == name.casefold():
                return self.with_not_tag(t)
        for t in self.not_tags:
            if t.casefold() == name.casefold():
                return self.with_tag(t)
        return self.with_tag(name)

    def without_tag(self, name: str) -> "JobFilter":
        return replace(self, tags=_drop(self.tags, name), not_tags=_drop(self.not_tags, name))
