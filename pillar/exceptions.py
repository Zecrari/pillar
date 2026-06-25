class PillarError(Exception):
    """Base class for all Pillar framework errors."""
    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str = None):
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class NotFoundError(PillarError):
    status_code = 404
    detail = "Resource not found"


class UnauthorizedError(PillarError):
    status_code = 401
    detail = "Authentication required"


class ForbiddenError(PillarError):
    status_code = 403
    detail = "Permission denied"


class ValidationError(PillarError):
    status_code = 422
    detail = "Validation failed"


class ConflictError(PillarError):
    status_code = 409
    detail = "Resource already exists"


class PillarContractError(PillarError):
    """
    Raised at startup when the type contracts between layers are violated.
    E.g. a repository returns str but the schema expects int.
    """
    status_code = 500
    detail = "Contract violation"


class ArchitectureViolationError(PillarError):
    """
    Raised at startup when a layer imports from an invalid layer.
    E.g. router.py imports directly from repository.py.
    """
    status_code = 500
    detail = "Architecture violation"
