import grpc
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths that don't require a valid JWT
_PUBLIC = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/cadastral/lookup",
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
        if not token:
            return JSONResponse(
                {"error": "UNAUTHORIZED", "message": "Empty bearer token"},
                status_code=401,
            )

        stub = getattr(request.app.state, "auth_stub", None)
        if stub is None:
            return JSONResponse(
                {"error": "AUTH_SERVICE_UNAVAILABLE", "message": "Auth service client is not initialized"},
                status_code=503,
            )

        try:
            import auth_pb2

            result = await stub.ValidateToken(
                auth_pb2.ValidateRequest(access_token=token),
                timeout=3,
            )
        except grpc.RpcError as exc:
            code = exc.code()
            status_code = 503 if code in {
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.DEADLINE_EXCEEDED,
            } else 401
            return JSONResponse(
                {"error": "UNAUTHORIZED", "message": exc.details() or "Token validation failed"},
                status_code=status_code,
            )

        if not result.valid:
            return JSONResponse(
                {"error": "UNAUTHORIZED", "message": "Invalid or expired token"},
                status_code=401,
            )

        request.state.user_id = result.user_id
        request.state.email = result.email
        return await call_next(request)
