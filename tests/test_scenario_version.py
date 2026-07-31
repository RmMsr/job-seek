from app.scenario_version import compute_version_hash


def test_hash_is_deterministic():
    scenario = {"description": "Remote ML roles"}
    criteria = [{"text": "Must be remote", "weight": "must"}]
    assert compute_version_hash(scenario, criteria) == compute_version_hash(scenario, criteria)


def test_hash_changes_with_description():
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({"description": "A"}, criteria)
    h2 = compute_version_hash({"description": "B"}, criteria)
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
