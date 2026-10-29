"""The one exception type for problems the user can fix (bad input, missing tool, unreachable cluster...)."""


class LabError(RuntimeError):
    """An expected, user-facing error. The message says what is wrong and how to fix it."""
