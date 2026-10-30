import json

import pytest

from app.glossary import MAX_REPLACEMENTS, GlossaryError, GlossaryStore, parse


def test_rules_are_applied_in_order():
    glossary = parse(
        {
            "hotwords": "  與會者有 鍾昀澔\n、廖苡蓁。 ",
            "replacements": [{"find": "鍾雲浩", "replace": "鍾昀澔"}, {"find": "廖以珍", "replace": "廖苡蓁"}],
        }
    )
    assert glossary.hotwords == "與會者有 鍾昀澔 、廖苡蓁。"
    assert glossary.apply("鍾雲浩說廖以珍會回覆") == "鍾昀澔說廖苡蓁會回覆"


def test_rules_that_change_nothing_are_dropped():
    glossary = parse({"hotwords": "", "replacements": [{"find": "OK", "replace": "OK"}]})
    assert glossary.replacements == ()


def test_replacement_may_delete_text():
    assert parse({"hotwords": "", "replacements": [{"find": "嗯", "replace": ""}]}).apply("嗯好") == "好"


@pytest.mark.parametrize(
    "data",
    [
        {"hotwords": ""},
        {"hotwords": "", "replacements": [{"find": "", "replace": "x"}]},
        {"hotwords": "", "replacements": [{"find": "a"}]},
        {"hotwords": 1, "replacements": []},
        {"hotwords": "", "replacements": [{"find": "a", "replace": "b"}] * (MAX_REPLACEMENTS + 1)},
    ],
)
def test_invalid_glossary_is_rejected(data):
    with pytest.raises(GlossaryError):
        parse(data)


def test_store_round_trips(tmp_path):
    store = GlossaryStore(tmp_path / "glossary.json")
    assert store.get().hotwords == ""
    saved = store.save(parse({"hotwords": "熱詞", "replacements": [{"find": "a", "replace": "b"}]}))
    assert store.get() == saved
    assert json.loads((tmp_path / "glossary.json").read_text(encoding="utf-8"))["hotwords"] == "熱詞"
