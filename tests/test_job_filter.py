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


def test_order_defaults_to_change_and_is_omitted():
    f = JobFilter.from_params({})
    assert f.order == "change"
    assert "order" not in f.query_params()
    assert not f.is_narrowed


def test_order_roundtrips_when_non_default():
    f = JobFilter.from_params({"order": "score"})
    assert f.order == "score"
    assert f.query_params()["order"] == "score"


def test_unknown_order_falls_back_to_change():
    assert JobFilter.from_params({"order": "bogus"}).order == "change"


def test_cleared_keeps_order():
    f = JobFilter.from_params({"status": "accepted", "org": "Acme", "order": "age"})
    c = f.cleared()
    assert c.order == "age" and not c.is_narrowed and c.status_tab == "accepted"


def test_for_status_keeps_order():
    assert JobFilter.from_params({"order": "age"}).for_status("trash").order == "age"


def test_org_none_sentinel():
    f = JobFilter.from_params({"org": "none"})
    assert f.org_none is True and f.org is None and f.is_narrowed
    assert f.query_params()["org"] == "none"


def test_org_none_cleared():
    assert JobFilter.from_params({"org": "none"}).cleared().org_none is False


def test_filter_parses_and_strips_query():
    f = JobFilter.from_params({"q": "  senior python  "})
    assert f.q == "senior python"
    assert f.searching is True


def test_filter_blank_query_is_not_searching():
    f = JobFilter.from_params({"status": "new"})
    assert f.q == ""
    assert f.searching is False
    assert "q" not in f.query_params()


def test_filter_query_params_roundtrip_keeps_status_and_q():
    f = JobFilter.from_params({"status": "accepted", "q": "acme"})
    assert f.query_params() == {"status": "accepted", "q": "acme"}


def test_statuses_default_is_single_new():
    f = JobFilter.from_params({})
    assert f.statuses == ("new",)
    assert f.status_tab == "new"
    assert f.is_multi is False


def test_statuses_comma_list_kept_in_order_and_deduped():
    f = JobFilter.from_params({"status": "accepted,new,accepted,bogus,trash"})
    assert f.statuses == ("accepted", "new", "trash")
    assert f.is_multi is True
    assert f.status_tab == "accepted"


def test_pending_is_a_valid_tab_recognized_by_from_params():
    f = JobFilter.from_params({"status": "trash,pending,new"})
    assert f.statuses == ("trash", "pending", "new")


def test_with_status_toggled_adds_pending_after_accepted():
    f = JobFilter.from_params({"status": "accepted"}).with_status_toggled("pending")
    assert f.statuses == ("accepted", "pending")


def test_statuses_all_junk_falls_back_to_new():
    assert JobFilter.from_params({"status": "bogus, ,"}).statuses == ("new",)


def test_query_params_joins_statuses():
    assert JobFilter.from_params({"status": "new,accepted"}).query_params()["status"] == "new,accepted"


def test_for_status_is_exclusive():
    f = JobFilter.from_params({"status": "new,accepted"}).for_status("rejected")
    assert f.statuses == ("rejected",)


def test_with_status_toggled_adds_in_valid_tabs_order():
    f = JobFilter.from_params({"status": "accepted"}).with_status_toggled("new")
    assert f.statuses == ("new", "accepted")


def test_with_status_toggled_removes_present_tab():
    f = JobFilter.from_params({"status": "new,accepted"}).with_status_toggled("new")
    assert f.statuses == ("accepted",)


def test_with_status_toggled_never_empties():
    assert JobFilter.from_params({"status": "new"}).with_status_toggled("new").statuses == ("new",)


def test_is_narrowed_true_when_only_query_set():
    f = JobFilter.from_params({"q": "rust"})
    assert f.is_narrowed is True
    assert not JobFilter.from_params({"status": "new,accepted"}).is_narrowed


def test_solo_defaults_false_and_round_trips_through_params():
    assert JobFilter.from_params({}).solo is False
    assert "solo" not in JobFilter.from_params({}).query_params()
    assert JobFilter.from_params({"solo": "1"}).solo is True
    assert JobFilter.from_params({"solo": "1"}).query_params()["solo"] == "1"


def test_for_status_sets_solo():
    f = JobFilter.from_params({"status": "new", "q": "rust"}).for_status("pending")
    assert f.solo is True
    assert f.query_params()["solo"] == "1"


def test_with_status_toggled_sets_solo():
    added = JobFilter.from_params({"status": "new"}).with_status_toggled("accepted")
    assert added.solo is True
    removed = JobFilter.from_params({"status": "new,accepted"}).with_status_toggled("accepted")
    assert removed.solo is True


def test_cleared_drops_query_and_filters_keeps_statuses_and_order():
    f = JobFilter.from_params(
        {"status": "new,accepted", "q": "rust", "scenario": "3", "source_id": "5",
         "org": "Acme", "order": "score"}
    )
    c = f.cleared()
    assert c.statuses == ("new", "accepted") and c.order == "score"
    assert c.q == "" and not c.is_narrowed
    assert "q" not in c.query_params()
