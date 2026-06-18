"""Reusable FastAPI dependencies for the API Gateway.

`require_admin` guards admin-only routes. It reads the role that
`AuthMiddleware` placed on `request.state` (extracted from the JWT "role"
claim via ValidateToken) and rejects non-admins with HTTP 403.
"""
from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    """Allow only users with the "admin" role.

    AuthMiddleware already validated the token and set `request.state.role`.
    Public/unauthenticated paths never reach admin routes, so the attribute
    is expected to be present; default to "user" defensively.
    """
    role = getattr(request.state, "role", "user")
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: требуются права администратора",
        )
