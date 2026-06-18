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
    headers_enabled=True,
)

# Стандартный строгий лимит для дорогих write-ручек (создание проверок/поисков).
WRITE_LIMIT = f"{settings.rate_limit_write_per_minute}/minute"
