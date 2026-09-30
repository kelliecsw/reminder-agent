class DomainError(Exception):
    """Base exception for domain-level validation errors."""


class InvalidReminderTargetError(DomainError):
    """Raised when a reminder does not target exactly one valid domain target."""


class InvalidRecurrenceError(DomainError):
    """Raised when a recurrence rule is invalid."""


class InvalidTimezoneError(DomainError):
    """Raised when an invalid IANA timezone is supplied."""


class ApplicationError(Exception):
    """Base exception for service-layer business rule failures."""


class NotFoundError(ApplicationError):
    """Raised when a requested record does not exist for the user."""


class InvalidStateTransitionError(ApplicationError):
    """Raised when a resource cannot move to the requested state."""


class InvalidOwnershipError(ApplicationError):
    """Raised when related records do not belong to the same user."""


class NoFutureOccurrenceError(ApplicationError):
    """Raised when a reminder schedule has no occurrence after now."""
