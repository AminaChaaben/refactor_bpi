from __future__ import annotations

import re

from connection_sources.graph import queries

PREFIX = "jira:example.atlassian.net:"
COLUMNS = [
    "relation", "via", "key", "type", "status", "title", "description",
    "acceptance_criteria", "gherkin", "steps", "run_status", "created", "updated",
]


def _aliases(branch: str) -> list[str]:
    """The column aliases a branch's final RETURN produces, in order."""
    returned = branch[branch.rindex("RETURN ") + len("RETURN "):]
    return re.findall(r"\bAS (\w+)", returned)


def test_story_context_builds_with_its_key():
    statement, params = queries.build("story-context", PREFIX, key="PROJ-1", limit=10)
    assert params["key"] == "PROJ-1"
    assert params["limit"] == 10
    assert params["prefix"] == PREFIX
    assert "$key" in statement and "$prefix" in statement


def test_every_story_context_branch_returns_the_same_columns():
    statement, _ = queries.build("story-context", PREFIX, key="PROJ-1")
    branches = statement.split("\nUNION ALL\n")
    relations = [re.search(r"RETURN (.+?) AS relation", b, re.S).group(1) for b in branches]
    assert relations == [
        "'self'", "'ancestor'", "'sibling'", "'link:' + type(r)", "'shared-tag'",
        "'test'", "'bug'", "'comment'", "'page'",
    ]
    for branch in branches:
        assert _aliases(branch) == COLUMNS, branch


def test_every_story_context_branch_is_scoped_to_the_site():
    statement, _ = queries.build("story-context", PREFIX, key="PROJ-1")
    for branch in statement.split("\nUNION ALL\n"):
        assert "s.uid STARTS WITH $prefix AND s.key = $key" in branch


def test_similar_issues_uses_the_alm_fulltext_index():
    statement, params = queries.build("similar-issues", PREFIX, q="login OR password", key="PROJ-1")
    assert "db.index.fulltext.queryNodes('alm_node_text', $q)" in statement
    assert "n.uid STARTS WITH $prefix" in statement
    assert params["q"] == "login OR password"
    # Same columns as story-context, so a consumer merges both; `score` is the extra last one.
    assert _aliases(statement) == COLUMNS
    assert statement.rstrip().endswith("score")


def test_missing_optional_parameters_default_to_null():
    _, params = queries.build("story-context", PREFIX)
    assert params["key"] is None and params["q"] is None


def test_explore_defaults_are_unchanged():
    _, params = queries.build("explore", PREFIX, limit=1000)
    assert params["limit"] == 300
    assert params["label"] is None and params["search"] is None


def test_similar_tests_uses_the_tests_fulltext_index_and_skips_the_story_own_tests():
    statement, params = queries.build("similar-tests", PREFIX, q="billing OR invoice", key="PROJ-1")
    assert "db.index.fulltext.queryNodes('alm_node_test_text', $q)" in statement
    assert "t.uid STARTS WITH $prefix AND t:Test" in statement
    assert "NOT coalesce($key, '') IN covers" in statement
    assert params["q"] == "billing OR invoice" and params["key"] == "PROJ-1"
    # The story-context columns (a consumer merges both), then `score` and the covered keys.
    # `steps` is returned as the variable collected above, so it has no `AS steps`.
    returned = statement[statement.rindex("RETURN "):]
    assert _aliases(statement) == [c for c in COLUMNS if c != "steps"]
    assert " steps, coalesce(run.status, t.run_status) AS run_status" in returned
    assert returned.rstrip().endswith("score, covers")
    assert "'similar-test' AS relation" in statement


def test_similar_tests_defaults_its_optional_parameters():
    _, params = queries.build("similar-tests", PREFIX)
    assert params["key"] is None and params["q"] is None


def test_story_changes_is_scoped_to_the_story_and_the_site():
    statement, params = queries.build("story-changes", PREFIX, key="PROJ-1")
    change = statement.split("\nUNION ALL\n")[0]
    assert "s.uid STARTS WITH $prefix AND s.key = $key" in change
    assert "(e)-[:CHANGED]->(f:FieldChange)" in change
    assert params["key"] == "PROJ-1"


def test_story_changes_defaults_pick_the_criteria_and_description_fields():
    _, params = queries.build("story-changes", PREFIX, key="PROJ-1")
    assert params["field_re"] == queries.DEFAULT_CHANGE_FIELD_RE
    assert params["field_ids"] == ""
    _, custom = queries.build("story-changes", PREFIX, key="PROJ-1", field_re="(?i).*ac.*",
                              field_ids="customfield_10010")
    assert custom["field_re"] == "(?i).*ac.*" and custom["field_ids"] == "customfield_10010"


def test_story_changes_branches_return_the_same_columns_and_a_meta_row_tells_unloaded_from_unedited():
    statement, _ = queries.build("story-changes", PREFIX, key="PROJ-1")
    branches = statement.split("\nUNION ALL\n")
    assert len(branches) == 2
    assert _aliases(branches[0]) == _aliases(branches[1]) == [
        "relation", "at", "who", "field", "field_id", "before", "after", "loaded"]
    assert "'meta' AS relation" in branches[1] and "e IS NOT NULL AS loaded" in branches[1]
    assert "e.uid STARTS WITH $prefix" in branches[1]


def test_default_field_regex_matches_the_usual_names_and_not_a_status_change():
    import re
    pattern = re.compile(queries.DEFAULT_CHANGE_FIELD_RE.replace("(?i)", ""), re.IGNORECASE)
    for name in ("Acceptance Criteria", "acceptance_criteria", "Critères d'acceptation", "Description"):
        assert pattern.fullmatch(name), name
    for name in ("status", "assignee", "Sprint"):
        assert not pattern.fullmatch(name), name


def test_test_dates_is_one_row_of_counts_scoped_to_the_site():
    statement, params = queries.build("test-dates", PREFIX)
    assert params["prefix"] == PREFIX
    assert statement.count("STARTS WITH $prefix") == 3
    assert "count(t.created) AS tests_with_created" in statement
    assert "count(t.steps_text) AS tests_with_steps_text" in statement
    returned = statement[statement.rindex("RETURN "):]
    for column in ("tests", "tests_with_created", "tests_with_updated", "tests_with_steps_text",
                   "change_events", "count(f) AS criteria_or_description_edits"):
        assert column in returned, column


def test_test_dates_is_listed_in_the_catalogue():
    assert "test-dates" in {q["name"] for q in queries.catalogue()}
