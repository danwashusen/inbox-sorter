"""Errors the user can act on.

The CLI reports any UserError as one line on stderr and exits 1, so a
module signals an expected failure by raising its own subclass.
"""


class UserError(Exception):
    """An expected failure with a message the user can act on."""


def error_reason(error: BaseException) -> str:
    """Word an error for a one-line message; prefers an OS error's strerror, which can be None."""
    return getattr(error, "strerror", None) or str(error) or type(error).__name__
