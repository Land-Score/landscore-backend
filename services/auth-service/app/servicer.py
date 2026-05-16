import uuid
import hashlib
import re
import secrets
import grpc
import structlog
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import auth_pb2
import auth_pb2_grpc

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import User, UserProfile, RefreshToken

log = structlog.get_logger()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.utcnow()


def _make_access_token(user_id: str, email: str) -> str:
    expire = _now() + timedelta(minutes=settings.jwt_access_ttl_minutes)
    return jwt.encode(
        {"sub": user_id, "email": email, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _make_raw_refresh() -> str:
    return secrets.token_urlsafe(48)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _clean_email(email: str) -> str:
    return email.lower().strip()


def _build_profile_pb(user: User, profile: UserProfile) -> auth_pb2.UserProfile:
    return auth_pb2.UserProfile(
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        client_type=profile.client_type,
        main_task=profile.main_task,
        region=profile.region,
        priority=list(profile.priority or []),
        risk_tolerance=profile.risk_tolerance,
        preferred_scenarios=list(profile.preferred_scenarios or []),
        organization=profile.organization,
        budget=float(profile.budget or 0.0),
        created_at=user.created_at.isoformat(),
    )


async def _issue_tokens(session, user: User, profile: UserProfile) -> auth_pb2.AuthResponse:
    access_token = _make_access_token(str(user.id), user.email)
    raw_refresh = _make_raw_refresh()
    token_hash = _hash_token(raw_refresh)
    expires_at = _now() + timedelta(days=settings.jwt_refresh_ttl_days)

    rt = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(rt)
    await session.flush()

    return auth_pb2.AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        profile=_build_profile_pb(user, profile),
    )


class AuthServicer(auth_pb2_grpc.AuthServiceServicer):
    async def Register(self, request, context):
        email = _clean_email(request.email)
        name = request.name.strip()

        if not email or not request.password or not name:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "email, password, and name are required")
            return
        if not EMAIL_RE.match(email):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid email format")
            return
        if len(request.password) < 8:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "password must be at least 8 characters")
            return

        async with AsyncSessionLocal() as session:
            try:
                existing = await session.execute(select(User).where(User.email == email))
                if existing.scalar_one_or_none():
                    await context.abort(grpc.StatusCode.ALREADY_EXISTS, "Email already registered")
                    return

                user = User(
                    id=uuid.uuid4(),
                    email=email,
                    name=name,
                    password_hash=pwd_context.hash(request.password),
                )
                session.add(user)
                await session.flush()

                profile = UserProfile(user_id=user.id)
                session.add(profile)
                await session.flush()

                resp = await _issue_tokens(session, user, profile)
                await session.commit()
                log.info("user_registered", user_id=str(user.id), email=user.email)
                return resp
            except IntegrityError:
                await session.rollback()
                await context.abort(grpc.StatusCode.ALREADY_EXISTS, "Email already registered")

    async def Login(self, request, context):
        email = _clean_email(request.email)

        if not email or not request.password:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "email and password are required")
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.email == email)
            )
            user = result.scalar_one_or_none()

            if not user or not pwd_context.verify(request.password, user.password_hash):
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid email or password")
                return

            profile_result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
            profile = profile_result.scalar_one_or_none()
            if not profile:
                profile = UserProfile(user_id=user.id)
                session.add(profile)
                await session.flush()

            resp = await _issue_tokens(session, user, profile)
            await session.commit()
            log.info("user_logged_in", user_id=str(user.id))
            return resp

    async def RefreshToken(self, request, context):
        if not request.refresh_token:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "refresh_token is required")
            return

        token_hash = _hash_token(request.refresh_token)

        async with AsyncSessionLocal() as session:
            rt_result = await session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == token_hash,
                    RefreshToken.revoked.is_(False),
                    RefreshToken.expires_at > _now(),
                ).with_for_update()
            )
            rt = rt_result.scalar_one_or_none()

            if not rt:
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or expired refresh token")
                return

            rt.revoked = True
            await session.flush()

            user_result = await session.execute(select(User).where(User.id == rt.user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                await context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
                return

            profile_result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
            profile = profile_result.scalar_one_or_none()
            if not profile:
                profile = UserProfile(user_id=user.id)
                session.add(profile)
                await session.flush()

            resp = await _issue_tokens(session, user, profile)
            await session.commit()
            return resp

    async def ValidateToken(self, request, context):
        if not request.access_token:
            return auth_pb2.ValidateResponse(valid=False)

        try:
            payload = jwt.decode(
                request.access_token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            user_id = payload["sub"]
            user_uuid = uuid.UUID(user_id)
            email = payload.get("email", "")
        except (JWTError, KeyError, ValueError):
            return auth_pb2.ValidateResponse(valid=False)

        async with AsyncSessionLocal() as session:
            user_result = await session.execute(select(User).where(User.id == user_uuid))
            user = user_result.scalar_one_or_none()
            if not user:
                return auth_pb2.ValidateResponse(valid=False)

            return auth_pb2.ValidateResponse(
                valid=True,
                user_id=user_id,
                email=email or user.email,
            )

    async def GetProfile(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
            return

        try:
            user_uuid = uuid.UUID(request.user_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id format")
            return

        async with AsyncSessionLocal() as session:
            user_result = await session.execute(select(User).where(User.id == user_uuid))
            user = user_result.scalar_one_or_none()
            if not user:
                await context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
                return

            profile_result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_uuid)
            )
            profile = profile_result.scalar_one_or_none()
            if not profile:
                profile = UserProfile(user_id=user_uuid)
                session.add(profile)
                await session.commit()
                await session.refresh(profile)

            return _build_profile_pb(user, profile)

    async def UpdateProfile(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
            return

        try:
            user_uuid = uuid.UUID(request.user_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id format")
            return

        async with AsyncSessionLocal() as session:
            user_result = await session.execute(select(User).where(User.id == user_uuid))
            user = user_result.scalar_one_or_none()
            if not user:
                await context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
                return

            profile_result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_uuid)
            )
            profile = profile_result.scalar_one_or_none()
            if not profile:
                profile = UserProfile(user_id=user_uuid)
                session.add(profile)

            if request.HasField("client_type"):
                profile.client_type = request.client_type
            if request.HasField("main_task"):
                profile.main_task = request.main_task
            if request.HasField("region"):
                profile.region = request.region
            if request.priority_set:
                profile.priority = list(request.priority)
            if request.HasField("risk_tolerance"):
                profile.risk_tolerance = request.risk_tolerance
            if request.preferred_scenarios_set:
                profile.preferred_scenarios = list(request.preferred_scenarios)
            if request.HasField("organization"):
                profile.organization = request.organization
            if request.HasField("budget"):
                profile.budget = request.budget

            await session.commit()
            await session.refresh(profile)
            return _build_profile_pb(user, profile)
