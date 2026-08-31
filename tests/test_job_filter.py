from app.job_filter import JobFilter


def test_defaults_to_new_tab():
    f = JobFilter.from_params({})
    assert f.status_tab == "new"
    assert not f.is_narrowed
    assert f.query_params() == {"status": "new"}


def test_unknown_tab_falls_back_to_new():
    assert JobFilter.from_params({"status": "bogus"}).status_tab == "new"


def test_scenario_id_param():
    f = JobFilter.from_params({"scenario": "3"})
    assert f.scenario_id == 3 and f.scenario_none is False and f.is_narrowed
    assert f.query_params() == {"status": "new", "scenario": "3"}


def test_scenario_none_sentinel():
    f = JobFilter.from_params({"scenario": "none"})
    assert f.scenario_none is True and f.scenario_id is None and f.is_narrowed
    assert f.query_params()["scenario"] == "none"


def test_legacy_scenario_id_alias():
    f = JobFilter.from_params({"scenario_id": "7"})
    assert f.scenario_id == 7


def test_source_and_org():
    f = JobFilter.from_params({"source_id": "5", "org": "Acme Corp"})
    assert f.source_id == 5 and f.org == "Acme Corp"
    assert f.query_params() == {"status": "new", "source_id": "5", "org": "Acme Corp"}


def test_blank_values_are_unset():
    f = JobFilter.from_params({"scenario": "", "source_id": "", "org": ""})
    assert not f.is_narrowed


def test_query_suffix_detail_wins():
    f = JobFilter.from_params({"status": "accepted", "org": "Acme"})
    assert f.query_suffix(detail=True) == "?detail=1"
    assert "status=accepted" in f.query_suffix()
    assert "org=Acme" in f.query_suffix()


def test_cleared_keeps_tab_only():
    f = JobFilter.from_params({"status": "rejected", "org": "Acme", "source_id": "2"})
    c = f.cleared()
    assert c.status_tab == "rejected" and not c.is_narrowed


def test_for_status_copies_filters():
    f = JobFilter.from_params({"org": "Acme"}).for_status("trash")
    assert f.status_tab == "trash" and f.org == "Acme"


def test_with_helpers_set_one_filter():
    base = JobFilter.from_params({"status": "accepted", "scenario": "none"})
    assert base.with_scenario_id("4").scenario_id == 4
    assert base.with_scenario_id("4").scenario_none is False
    assert base.with_source_id(9).source_id == 9
    assert base.with_org("Globex").org == "Globex"
