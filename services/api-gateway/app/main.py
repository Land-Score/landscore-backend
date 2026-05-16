from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.routers import auth, checks, searches, documents
from app.middleware.auth import AuthMiddleware

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="LandScore AI — API Gateway", version="0.1.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(checks.router, prefix="/api/checks", tags=["checks"])
app.include_router(searches.router, prefix="/api/searches", tags=["searches"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api-gateway"}
