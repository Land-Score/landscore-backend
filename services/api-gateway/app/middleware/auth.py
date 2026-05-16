from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from jose import JWTError, jwt

from app.config import settings

PUBLIC_PATHS = {"/health", "/api/auth/register", "/api/auth/login", "/api/auth/refresh"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            request.state.user_id = payload["sub"]
            request.state.email = payload.get("email", "")
        except JWTError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Invalid token"}, status_code=401)

        return await call_next(request)
