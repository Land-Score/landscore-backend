import grpc
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthServicer:
    """Implements auth.proto AuthService."""

    # After `make proto`, these methods will be typed against generated classes.

    async def Register(self, request, context):
        # 1. Check email not taken (DB query)
        # 2. Hash password
        # 3. Create User + UserProfile in DB
        # 4. Issue tokens
        # 5. Run Profile Setup Agent (Yandex AI) if profile fields provided
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Not implemented yet")

    async def Login(self, request, context):
        # 1. Find user by email
        # 2. Verify password
        # 3. Issue tokens
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Not implemented yet")

    async def ValidateToken(self, request, context):
        try:
            payload = jwt.decode(
                request.access_token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            # return ValidateResponse(valid=True, user_id=payload["sub"], email=payload["email"])
        except Exception:
            pass  # return ValidateResponse(valid=False)

    def _make_access_token(self, user_id: str, email: str) -> str:
        expire = datetime.utcnow() + timedelta(minutes=settings.jwt_access_ttl_minutes)
        return jwt.encode(
            {"sub": user_id, "email": email, "exp": expire},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

    async def GetProfile(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def UpdateProfile(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
