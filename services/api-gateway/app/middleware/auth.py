from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# Paths that don't require a valid JWT
_PUBLIC = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/refresh",
}

# Prefixes that are always public (Swagger static assets)
_PUBLIC_PREFIXES = ("/docs/", "/redoc/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _PUBLIC or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "UNAUTHORIZED", "message": "Missing Authorization header"},
                status_code=401,
            )

        token = auth_header.removeprefix("Bearer ").strip()
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            request.state.user_id = payload["sub"]
            request.state.email = payload.get("email", "")
        except JWTError as exc:
            return JSONResponse(
                {"error": "UNAUTHORIZED", "message": f"Invalid token: {exc}"},
                status_code=401,
            )

        return await call_next(request)
