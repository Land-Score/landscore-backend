import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.clients import setup_clients, close_clients
from app.config import settings
from app.middleware.auth import AuthMiddleware
from app.models import HealthResponse, ServiceHealthItem
from app.routers import auth, cadastral, checks, searches, documents

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("gateway_starting", version="0.1.0")
    await setup_clients(app)
    yield
    await close_clients(app)
    log.info("gateway_stopped")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="LandScore AI — API Gateway",
    description="""
## LandScore AI — платформа анализа земельных участков

Единая точка входа для всех клиентов. Все запросы аутентифицируются через JWT.

---

### Сценарий 1 — Проверка конкретного участка

1. **POST** `/api/checks` — запустить анализ участка (по кадастровому номеру / адресу)
2. **GET** `/api/checks/{id}/status` — опрашивать прогресс каждые 2 сек (пока `status != completed`)
3. **GET** `/api/checks/{id}/report` — получить полный отчёт (доступен только при `status=completed`)

### Сценарий 2 — Поиск участка под задачу

1. **POST** `/api/searches` — описать задачу в свободной форме
2. **GET** `/api/searches/{id}/criteria` — получить критерии, извлечённые AI
3. **PUT** `/api/searches/{id}/criteria` — подтвердить/скорректировать критерии
4. **GET** `/api/searches/{id}/results` — список найденных кандидатов
5. **GET** `/api/searches/{id}/recommendation` — финальная рекомендация

---

### Аутентификация

Все запросы (кроме `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`)
требуют заголовок:
```
Authorization: Bearer <access_token>
```

Токен выдаётся при регистрации и логине. Время жизни access_token — 60 минут.
Для обновления используйте `/api/auth/refresh` с `refresh_token`.
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
    },
    contact={"name": "LandScore AI", "url": "http://localhost:3000"},
    license_info={"name": "Proprietary"},
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth middleware ───────────────────────────────────────────────────────────
app.add_middleware(AuthMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,      prefix="/api/auth",      tags=["Аутентификация"])
app.include_router(cadastral.router, prefix="/api/cadastral", tags=["Кадастровые данные"])
app.include_router(checks.router,    prefix="/api/checks",    tags=["Проверка участка"])
app.include_router(searches.router,  prefix="/api/searches",  tags=["Поиск участка"])
app.include_router(documents.router, prefix="/api/documents", tags=["Документы"])


# ── System endpoints ──────────────────────────────────────────────────────────

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check всех сервисов",
    description="Проверяет TCP-доступность всех downstream gRPC-сервисов.",
)
async def health_check() -> HealthResponse:
    downstream = [
        ("auth-service",     settings.auth_grpc),
        ("check-service",    settings.check_grpc),
        ("search-service",   settings.search_grpc),
        ("document-service", settings.document_grpc),
        ("data-collector",   settings.data_collector_grpc),
        ("geo-service",      settings.geo_grpc),
    ]

    async def probe(name: str, address: str) -> ServiceHealthItem:
        host, port_str = address.rsplit(":", 1)
        port = int(port_str)
        t0 = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=2.0
            )
            writer.close()
            await writer.wait_closed()
            latency = int((time.monotonic() - t0) * 1000)
            return ServiceHealthItem(name=name, address=address, status="ok", latency_ms=latency)
        except Exception as exc:
            return ServiceHealthItem(
                name=name, address=address, status="unreachable", error=str(exc)
            )

    results = await asyncio.gather(*[probe(n, a) for n, a in downstream])
    overall = "ok" if all(s.status == "ok" for s in results) else "degraded"
    return HealthResponse(status=overall, version="0.1.0", services=list(results))


# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_exception", path=request.url.path, error=str(exc), exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_SERVER_ERROR", "message": "Unexpected server error"},
    )


# ── OpenAPI security scheme ───────────────────────────────────────────────────

_PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/cadastral/lookup",
}


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Access token полученный при /api/auth/login или /api/auth/register",
    }

    for path, methods in schema.get("paths", {}).items():
        if path not in _PUBLIC_PATHS:
            for method_data in methods.values():
                if isinstance(method_data, dict):
                    method_data.setdefault("security", [{"BearerAuth": []}])

    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi
