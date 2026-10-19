"""An in-memory Mailbox for tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from inbox_sorter.gmail import PROCESSED_LABEL, LabelInfo, MailboxError
from inbox_sorter.types import SYSTEM_LABELS, Email


class FakeMailbox:
    """Holds emails (newest first), bodies and labels in memory.

    Label names resolve to IDs through ``labels``, as in Gmail, so a label
    missing from ``labels`` matches no message. Writes are recorded in
    ``modify_calls`` and ``created``; ``fail_modify`` makes modify raise a
    500 for the listed message IDs.
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
        self.modify_calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        self.created: list[str] = []
        self.label_ids_calls = 0
        self.fail_modify: set[str] = set()

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
                return replace(email, body=None)  # metadata only, as in Gmail
        raise MailboxError(f"no message {message_id!r}", status=404)

    def get_body(self, message_id: str, max_chars: int) -> str:
        self.get_message(message_id)  # an unknown ID raises, as in Gmail
        return self.bodies.get(message_id, "")[:max_chars]

    def list_labels(self) -> list[LabelInfo]:
        return list(self.labels)

    def label_ids(self) -> dict[str, str]:
        self.label_ids_calls += 1
        return {label.name: label.id for label in self.labels}

    def create_label(self, name: str) -> str:
        if self._label_id(name) is not None:
            raise MailboxError(f"label {name!r} exists", status=409)
        self.created.append(name)
        # Counted from 1 per fake, so it never collides with fixture IDs such as Label_9.
        label = LabelInfo(f"Label_new_{len(self.created)}", name, system=False, message_count=0)
        self.labels.append(label)
        return label.id

    def modify(
        self, message_id: str, add_label_ids: Sequence[str], remove_label_ids: Sequence[str]
    ) -> None:
        self.modify_calls.append((message_id, tuple(add_label_ids), tuple(remove_label_ids)))
        if message_id in self.fail_modify:
            raise MailboxError(f"modify failed for {message_id!r}", status=500)
        known = {label.id for label in self.labels} | SYSTEM_LABELS
        unknown = [label_id for label_id in add_label_ids if label_id not in known]
        if unknown:
            raise MailboxError(f"unknown label ID {unknown[0]!r}", status=400)
        for position, email in enumerate(self.emails):
            if email.id == message_id:
                kept = email.label_ids - set(remove_label_ids)
                self.emails[position] = replace(email, label_ids=kept | set(add_label_ids))
                return
        raise MailboxError(f"no message {message_id!r}", status=404)

    def _label_id(self, name: str) -> str | None:
        return next((label.id for label in self.labels if label.name == name), None)
