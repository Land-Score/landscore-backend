import uuid
import hashlib
import re
import secrets
import grpc
import structlog
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import auth_pb2
import auth_pb2_grpc

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import User, UserProfile, RefreshToken

log = structlog.get_logger()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Допустимые значения enum-полей профиля (см. CLAUDE.md / Personalization).
CLIENT_TYPES = {"developer", "agroholding", "private", "legal", "investor"}
MAIN_TASKS = {"land_plot_selection", "land_check", "portfolio"}
RISK_TOLERANCES = {"low", "medium", "high"}
PREFERRED_SCENARIOS = {"construction", "agriculture", "resale", "development"}

# Ограничения длины строковых полей (должны совпадать с models.py).
MAX_NAME_LEN = 255
MAX_REGION_LEN = 255
MAX_ORGANIZATION_LEN = 255
MAX_CLIENT_TYPE_LEN = 50
MAX_MAIN_TASK_LEN = 50
MAX_RISK_TOLERANCE_LEN = 20

# Сколько свежих refresh-токенов на пользователя оставлять после ротации.
MAX_LIVE_REFRESH_TOKENS = 5


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


def _validate_profile_fields(
    *,
    client_type: str | None = None,
    main_task: str | None = None,
    region: str | None = None,
    organization: str | None = None,
    risk_tolerance: str | None = None,
    preferred_scenarios: list[str] | None = None,
) -> str | None:
    """Проверяет длины и допустимые значения полей профиля.
    Возвращает текст ошибки (рус.) или None, если всё валидно."""
    if client_type is not None:
        if len(client_type) > MAX_CLIENT_TYPE_LEN:
            return f"client_type не должен превышать {MAX_CLIENT_TYPE_LEN} символов"
        if client_type and client_type not in CLIENT_TYPES:
            return f"недопустимое значение client_type: {client_type}"
    if main_task is not None:
        if len(main_task) > MAX_MAIN_TASK_LEN:
            return f"main_task не должен превышать {MAX_MAIN_TASK_LEN} символов"
        if main_task and main_task not in MAIN_TASKS:
            return f"недопустимое значение main_task: {main_task}"
    if region is not None and len(region) > MAX_REGION_LEN:
        return f"region не должен превышать {MAX_REGION_LEN} символов"
    if organization is not None and len(organization) > MAX_ORGANIZATION_LEN:
        return f"organization не должен превышать {MAX_ORGANIZATION_LEN} символов"
    if risk_tolerance is not None:
        if len(risk_tolerance) > MAX_RISK_TOLERANCE_LEN:
            return f"risk_tolerance не должен превышать {MAX_RISK_TOLERANCE_LEN} символов"
        if risk_tolerance and risk_tolerance not in RISK_TOLERANCES:
            return f"недопустимое значение risk_tolerance: {risk_tolerance}"
    if preferred_scenarios is not None:
        for scenario in preferred_scenarios:
            if scenario not in PREFERRED_SCENARIOS:
                return f"недопустимый сценарий preferred_scenarios: {scenario}"
    return None


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


async def _cleanup_refresh_tokens(session, user_id) -> None:
    """Удаляет протухшие/отозванные refresh-токены пользователя и
    ограничивает число живых токенов наиболее свежими."""
    now = _now()
    # Убираем всё, что уже невалидно (истекло или отозвано).
    await session.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id,
            (RefreshToken.expires_at <= now) | (RefreshToken.revoked.is_(True)),
        )
    )

    # Ограничиваем число оставшихся живых токенов самыми свежими.
    live_result = await session.execute(
        select(RefreshToken.id)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > now,
        )
        .order_by(RefreshToken.created_at.desc())
    )
    live_ids = live_result.scalars().all()
    stale_ids = live_ids[MAX_LIVE_REFRESH_TOKENS - 1:]
    if stale_ids:
        await session.execute(
            delete(RefreshToken).where(RefreshToken.id.in_(stale_ids))
        )


async def _issue_tokens(session, user: User, profile: UserProfile) -> auth_pb2.AuthResponse:
    access_token = _make_access_token(str(user.id), user.email)
    raw_refresh = _make_raw_refresh()
    token_hash = _hash_token(raw_refresh)
    expires_at = _now() + timedelta(days=settings.jwt_refresh_ttl_days)

    await _cleanup_refresh_tokens(session, user.id)

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


async def _get_or_create_profile(session, user_id) -> UserProfile:
    """Возвращает профиль пользователя, создавая его при первом обращении.
    Защищён от гонки одновременного создания: INSERT выполняется в SAVEPOINT,
    при IntegrityError откатывается только он, и профиль перечитывается."""
    result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile:
        return profile

    try:
        async with session.begin_nested():
            profile = UserProfile(user_id=user_id)
            session.add(profile)
            await session.flush()
    except IntegrityError:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one()
    return profile


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
        if len(name) > MAX_NAME_LEN:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"имя не должно превышать {MAX_NAME_LEN} символов",
            )
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

            profile = await _get_or_create_profile(session, user.id)

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

            profile = await _get_or_create_profile(session, user.id)

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

        try:
            async with AsyncSessionLocal() as session:
                user_result = await session.execute(select(User).where(User.id == user_uuid))
                user = user_result.scalar_one_or_none()
        except SQLAlchemyError:
            # Недоступность БД не должна выглядеть как невалидный токен:
            # возвращаем UNAVAILABLE, чтобы шлюз отдал 503, а не 401.
            log.warning("validate_token_db_unavailable", user_id=user_id)
            await context.abort(grpc.StatusCode.UNAVAILABLE, "сервис аутентификации временно недоступен")
            return

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

            profile = await _get_or_create_profile(session, user_uuid)
            await session.commit()

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

        validation_error = _validate_profile_fields(
            client_type=request.client_type if request.HasField("client_type") else None,
            main_task=request.main_task if request.HasField("main_task") else None,
            region=request.region if request.HasField("region") else None,
            organization=request.organization if request.HasField("organization") else None,
            risk_tolerance=request.risk_tolerance if request.HasField("risk_tolerance") else None,
            preferred_scenarios=list(request.preferred_scenarios) if request.preferred_scenarios_set else None,
        )
        if validation_error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, validation_error)
            return

        async with AsyncSessionLocal() as session:
            user_result = await session.execute(select(User).where(User.id == user_uuid))
            user = user_result.scalar_one_or_none()
            if not user:
                await context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
                return

            profile = await _get_or_create_profile(session, user_uuid)

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
