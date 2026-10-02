from __future__ import annotations

from connection_sources.graph.extract_xray import IssueIndex, extract_tests, steps_text
from connection_sources.graph.ontology import load_ontology


def test_steps_text_is_one_line_per_step_with_action_data_and_result():
    steps = [
        {"action": "Open the billing page", "data": "", "result": "the page opens"},
        {"action": "Click Export", "data": "format=PDF", "result": "the invoice is exported"},
    ]
    assert steps_text(steps) == (
        "Open the billing page | the page opens\n"
        "Click Export | format=PDF | the invoice is exported"
    )


def test_steps_text_is_none_without_usable_steps():
    assert steps_text(None) is None
    assert steps_text([]) is None
    assert steps_text([{"action": "", "data": None, "result": " "}, "not a step"]) is None


def test_a_test_node_carries_its_steps_text_for_the_full_text_index():
    index = IssueIndex("example.atlassian.net")
    tests = [{
        "issueId": "1001",
        "jira": {"key": "PROJ-2", "summary": "Export an invoice"},
        "testType": {"name": "Manual", "kind": "Steps"},
        "steps": [{"id": "s1", "action": "Open the billing page", "result": "the page opens"}],
    }]
    batch = extract_tests(tests, site="example.atlassian.net", index=index, ontology=load_ontology())
    node = next(n for n in batch.nodes.values() if "Test" in n.labels)
    assert node.props["steps_text"] == "Open the billing page | the page opens"
