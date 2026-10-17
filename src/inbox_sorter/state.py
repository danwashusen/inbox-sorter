"""Turn an email into the state a decision engine reads."""

from collections.abc import Mapping

EMAIL_FIELDS = ("from", "subject", "snippet")


def email_state(email: Mapping[str, str]) -> dict[str, str]:
    """Keep only the fields engines should see; missing ones become ""."""
    return {field: email.get(field, "") for field in EMAIL_FIELDS}
