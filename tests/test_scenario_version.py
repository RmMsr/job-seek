from app.scenario_version import compute_version_hash, compute_profile_hash


def test_hash_is_deterministic():
    scenario = {"description": "Remote ML roles"}
    criteria = [{"text": "Must be remote", "weight": "must"}]
    assert compute_version_hash(scenario, criteria) == compute_version_hash(scenario, criteria)


def test_hash_changes_with_description():
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({"description": "A"}, criteria)
    h2 = compute_version_hash({"description": "B"}, criteria)
    assert h1 != h2


def test_hash_changes_with_name():
    description = "Remote ML roles"
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({"name": "A", "description": description}, criteria)
    h2 = compute_version_hash({"name": "B", "description": description}, criteria)
    assert h1 != h2


def test_hash_changes_with_criteria():
    scenario = {"description": "Remote ML roles"}
    h1 = compute_version_hash(scenario, [{"text": "Must be remote", "weight": "must"}])
    h2 = compute_version_hash(scenario, [{"text": "Must be senior", "weight": "must"}])
    assert h1 != h2


def test_hash_unaffected_by_criteria_order():
    scenario = {"description": "Remote ML roles"}
    c1 = {"text": "Must be remote", "weight": "must"}
    c2 = {"text": "Prefer Python", "weight": "prefer"}
    assert compute_version_hash(scenario, [c1, c2]) == compute_version_hash(scenario, [c2, c1])


def test_hash_handles_missing_description_key():
    assert compute_version_hash({}, []) == compute_version_hash({"description": ""}, [])


def test_hash_handles_missing_name_key():
    assert compute_version_hash({}, []) == compute_version_hash({"name": ""}, [])


def test_hash_changes_with_gate_threshold():
    scenario = {"description": "Remote ML roles"}
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({**scenario, "gate_threshold": 0.7}, criteria)
    h2 = compute_version_hash({**scenario, "gate_threshold": 0.9}, criteria)
    assert h1 != h2


def test_profile_hash_is_deterministic():
    assert compute_profile_hash("Senior ML engineer") == compute_profile_hash("Senior ML engineer")


def test_profile_hash_changes_with_content():
    assert compute_profile_hash("A") != compute_profile_hash("B")


def test_profile_hash_handles_empty_string():
    assert compute_profile_hash("") == compute_profile_hash("")
