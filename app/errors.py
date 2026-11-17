"""领域异常到 HTTP 的映射，页面与接口共用一套错误体。"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status = 400
    code = "E_DOMAIN"

    def __init__(self, message: str, code: str | None = None, status: int | None = None, **extra):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.extra = extra

    def payload(self) -> dict:
        body = {"error": self.code, "message": self.message}
        body.update(self.extra)
        return body


class BadInput(DomainError):
    status = 400
    code = "E_BAD_INPUT"


class NotFound(DomainError):
    status = 404
    code = "E_NOT_FOUND"


class Conflict(DomainError):
    status = 409
    code = "E_CONFLICT"


class EmptyDataset(BadInput):
    status = 422
    code = "E_EMPTY_DATASET"


class TruncatedInput(BadInput):
    code = "E_TRUNCATED"


async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.payload())
