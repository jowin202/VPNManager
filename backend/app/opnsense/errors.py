from __future__ import annotations


class OpnsenseError(Exception):
    """Error while talking to OPNsense. Never contains request payloads
    (they may contain private keys)."""

    def __init__(self, message: str, *, path: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.path = path
        self.status = status

    def to_dict(self) -> dict:
        return {"error": type(self).__name__, "message": self.message, "path": self.path, "status": self.status}


class OpnsenseUnreachable(OpnsenseError):
    pass


class OpnsenseAuthError(OpnsenseError):
    """401/403: wrong credentials or missing privileges."""


class OpnsenseNotFound(OpnsenseError):
    """404: endpoint does not exist (plugin missing, version too old)."""


class OpnsenseValidationError(OpnsenseError):
    def __init__(self, message: str, validations: dict, *, path: str | None = None):
        super().__init__(message, path=path)
        self.validations = validations

    def to_dict(self) -> dict:
        return {**super().to_dict(), "validations": self.validations}


class SchemaError(OpnsenseError):
    """An expected field does not exist in the live schema. Deliberately a hard
    error: field names are never guessed."""
