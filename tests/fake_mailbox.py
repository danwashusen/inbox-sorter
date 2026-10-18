"""An in-memory Mailbox for tests."""

from collections.abc import Mapping, Sequence

from inbox_sorter.gmail import PROCESSED_LABEL, LabelInfo, MailboxError
from inbox_sorter.types import Email


class FakeMailbox:
    """Holds emails (newest first), bodies and labels in memory.

    Label names resolve to IDs through ``labels``, as in Gmail, so a label
    missing from ``labels`` matches no message.
    """

    def __init__(
        self,
        emails: Sequence[Email] = (),
        bodies: Mapping[str, str] | None = None,
        labels: Sequence[LabelInfo] = (),
    ) -> None:
        self.emails = list(emails)
        self.bodies = dict(bodies or {})
        self.labels = list(labels)

    def list_unprocessed(
        self, page_token: str | None = None, page_size: int = 100
    ) -> tuple[list[str], str | None]:
        processed = self._label_id(PROCESSED_LABEL)
        ids = [
            email.id
            for email in self.emails
            if "INBOX" in email.label_ids and processed not in email.label_ids
        ]
        start = int(page_token) if page_token else 0
        end = start + page_size
        return ids[start:end], str(end) if end < len(ids) else None

    def list_by_label(self, label: str, limit: int) -> list[str]:
        label_id = self._label_id(label)
        if label_id is None:
            return []
        return [email.id for email in self.emails if label_id in email.label_ids][: max(limit, 0)]

    def get_message(self, message_id: str) -> Email:
        for email in self.emails:
            if email.id == message_id:
                return email
        raise MailboxError(f"no message {message_id!r}", status=404)

    def get_body(self, message_id: str, max_chars: int) -> str:
        self.get_message(message_id)  # an unknown ID raises, as in Gmail
        return self.bodies.get(message_id, "")[:max_chars]

    def list_labels(self) -> list[LabelInfo]:
        return list(self.labels)

    def _label_id(self, name: str) -> str | None:
        return next((label.id for label in self.labels if label.name == name), None)
