from dataclasses import replace

import pytest

from inbox_sorter.config import ConfigError
from inbox_sorter.engine import Choice, Noul, Score
from inbox_sorter.questions import (
    CATEGORY,
    NEEDS_REPLY,
    URGENCY,
    URGENCY_LEVELS,
    build_questions,
)
from tests.factories import ALERTS, RECEIPTS, make_config


def test_the_three_questions_have_their_types():
    questions = build_questions(make_config())

    assert list(questions) == [CATEGORY, NEEDS_REPLY, URGENCY]
    assert isinstance(questions[CATEGORY], Choice)
    assert isinstance(questions[NEEDS_REPLY], Noul)
    assert isinstance(questions[URGENCY], Score)
    assert questions[URGENCY].levels == URGENCY_LEVELS == ("Can wait", "This week", "Today")


def test_only_enabled_categories_are_offered_in_config_order():
    off = replace(RECEIPTS, id="off", gmail_label="Off", enabled=False)
    questions = build_questions(make_config(ALERTS, off, RECEIPTS))

    assert questions[CATEGORY].options == {
        "alerts": ALERTS.description,
        "receipts": RECEIPTS.description,
    }


def test_no_enabled_category_is_a_config_error():
    config = make_config(replace(RECEIPTS, enabled=False))

    with pytest.raises(ConfigError, match="no category is enabled"):
        build_questions(config)
