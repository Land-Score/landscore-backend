"""Shared slowapi Limiter instance.

Lives in its own module so routers can apply per-route `@limiter.limit(...)`
decorators without importing from `app.main` (which would be a circular import).

Default limits are driven by `settings.rate_limit_per_minute` and applied to
every route via `SlowAPIMiddleware` (wired in `app.main`). Write endpoints add a
stricter per-route limit on top via the decorator.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    # headers_enabled MUST stay False: with per-route @limiter.limit decorators,
    # slowapi's header injection requires the endpoint to RETURN a starlette
    # Response, but our endpoints return Pydantic models -> it would raise and 500.
    # Enforcement (429 on real overflow) still works without the X-RateLimit headers.
    headers_enabled=False,
    # A rate limiter must never 500 the app on its own internal error (storage /
    # header-injection quirks, or the middleware+decorator double-eval). Real
    # limit hits still raise RateLimitExceeded -> 429; internal errors are
    # swallowed and the request is allowed through.
    swallow_errors=True,
)

# Стандартный строгий лимит для дорогих write-ручек (создание проверок/поисков).
WRITE_LIMIT = f"{settings.rate_limit_write_per_minute}/minute"
