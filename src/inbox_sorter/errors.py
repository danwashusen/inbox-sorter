"""Errors the user can act on.

The CLI reports any UserError as one line on stderr and exits 1, so a
module signals an expected failure by raising its own subclass.
"""


class UserError(Exception):
    """An expected failure with a message the user can act on."""
