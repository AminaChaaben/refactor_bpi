"""Acceptance criteria derived deterministically from a description when the AC field is empty."""

from __future__ import annotations

from connection_sources.models import AlmRecord
from connection_sources.sync.normalize import (
    DESCRIPTION_CRITERIA_LABEL,
    acceptance_criteria_lines,
    criteria_from_description,
    test_details_of as details_of,
)


def adf(*blocks):
    return {"type": "doc", "content": list(blocks)}


def para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def bullets(*items):
    return {"type": "bulletList", "content": [
        {"type": "listItem", "content": [para(i)]} for i in items]}


def test_ok_sentence_keeps_its_first_word():
    assert acceptance_criteria_lines("Ok. le fichier est accepté") == ["Ok. le fichier est accepté"]
    assert acceptance_criteria_lines("1. un\n- deux\na) trois") == ["un", "deux", "trois"]


def test_only_the_section_under_the_heading_is_taken_from_an_adf_description():
    value = adf(para("Contexte du besoin"), para("Critères d'acceptation :"),
                bullets("Un fichier valide est accepté", "Un fichier trop gros est refusé"),
                para("Notes:"), para("hors périmètre"))
    assert criteria_from_description(value) == [
        "Un fichier valide est accepté", "Un fichier trop gros est refusé"]


def test_a_description_without_a_list_or_heading_yields_nothing():
    assert criteria_from_description("Some prose. More prose.") == []


def test_list_items_are_taken_when_there_is_no_heading():
    value = adf(para("A participant uploads a CSV file."), bullets("one", "two"))
    assert criteria_from_description(value) == ["one", "two"]
    assert criteria_from_description("Intro\n- a bullet\n2. numbered") == ["a bullet", "numbered"]


def record(fields):
    return AlmRecord(system="jira", id="1", key="PT-1", title="t", status="s", type="Story",
                     url="u", raw={"fields": fields})


def test_details_carry_derived_criteria_only_when_the_field_is_empty():
    rec = record({"description": "Acceptance criteria:\n- a\n- b"})
    details = dict(details_of(rec, {"acceptance_criteria": "customfield_1", "description": "description"}))
    assert details[DESCRIPTION_CRITERIA_LABEL] == "a\nb"
    rec2 = record({
        "description": "Acceptance criteria:\n- a", "customfield_1": "- x"})
    details2 = dict(details_of(rec2, {"acceptance_criteria": "customfield_1", "description": "description"}))
    assert DESCRIPTION_CRITERIA_LABEL not in details2 and details2["acceptance_criteria"] == "x"
