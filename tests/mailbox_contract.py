"""Checks every Mailbox must pass, whatever is behind it.

The mailbox needs at least one unprocessed inbox message.
"""

from inbox_sorter.gmail import LabelInfo, Mailbox
from inbox_sorter.types import Email


def assert_conforms(mailbox: Mailbox) -> None:
    ids, token = mailbox.list_unprocessed()
    assert ids, "the contract needs at least one unprocessed inbox message"
    assert all(isinstance(message_id, str) for message_id in ids)
    assert token is None or isinstance(token, str)

    message_id = ids[0]
    email = mailbox.get_message(message_id)
    assert isinstance(email, Email)
    assert email.id == message_id
    assert "INBOX" in email.label_ids

    body = mailbox.get_body(message_id, 50)
    assert isinstance(body, str)
    assert len(body) <= 50

    labels = mailbox.list_labels()
    assert all(isinstance(label, LabelInfo) for label in labels)
    assert any(label.id == "INBOX" and label.system for label in labels)

    assert len(mailbox.list_by_label("INBOX", 3)) <= 3
