"""Errors the user can act on.

The CLI reports any UserError as one line on stderr and exits 1, so a
module signals an expected failure by raising its own subclass.
"""


class UserError(Exception):
    """An expected failure with a message the user can act on."""


def os_error_reason(error: OSError) -> str:
    """Word an OS error for a one-line message; its strerror can be None."""
    return error.strerror or str(error) or type(error).__name__
