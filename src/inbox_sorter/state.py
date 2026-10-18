"""Turn an email into the state a decision engine reads."""

from collections.abc import Mapping

from inbox_sorter.types import Email

EMAIL_FIELDS = ("from", "subject", "snippet")


def email_state(email: Mapping[str, str]) -> dict[str, str]:
    """Keep only the fields engines should see; missing ones become ""."""
    return {field: email.get(field, "") for field in EMAIL_FIELDS}


def message_state(email: Email) -> dict[str, str]:
    """The state for a fetched message; adds ``body`` once it has been fetched."""
    state = email_state({"from": email.sender, "subject": email.subject, "snippet": email.snippet})
    if email.body is not None:
        state["body"] = email.body
    return state
