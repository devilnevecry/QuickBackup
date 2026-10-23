"""Exceptions with user-facing messages."""


class QuickBackupError(Exception):
    """Base class for all expected errors."""


class SourceError(QuickBackupError):
    """A source path is missing, unreadable or ambiguous."""


class DestinationError(QuickBackupError):
    """The destination cannot be used safely."""


class ArchiveError(QuickBackupError):
    """The archive could not be created."""


class VerificationError(QuickBackupError):
    """The archive does not match its manifest."""
