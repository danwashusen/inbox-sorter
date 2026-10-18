from dataclasses import FrozenInstanceError

import pytest

from inbox_sorter.types import Decision, Email, RunStarted


def make_email(**overrides):
    fields = dict(id="m1", sender="a@example.com", subject="Hi", snippet="", label_ids=frozenset())
    return Email(**(fields | overrides))


def test_email_body_is_none_until_fetched():
    assert make_email().body is None


@pytest.mark.parametrize(
    "value, field",
    [
        (make_email(), "subject"),
        (
            Decision("m1", "receipts", 0.9, False, False, False, False, ("Receipts",), ("INBOX",)),
            "category",
        ),
        (RunStarted("run-1", True, "fake", None), "total"),
    ],
)
def test_core_types_are_frozen(value, field):
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, "changed")
