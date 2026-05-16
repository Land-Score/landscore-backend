import grpc
from fastapi import APIRouter, Request

from app.errors import raise_for_grpc
from app.models import (
    RegisterRequest, LoginRequest, RefreshRequest,
    UpdateProfileRequest, AuthResponse, TokenResponse, UserProfileResponse,
)

router = APIRouter()


def _profile_from_proto(p) -> UserProfileResponse:
    return UserProfileResponse(
        user_id=p.user_id,
        email=p.email,
        name=p.name,
        client_type=p.client_type,
        main_task=p.main_task,
        region=p.region,
        priority=list(p.priority),
        risk_tolerance=p.risk_tolerance,
        preferred_scenarios=list(p.preferred_scenarios),
        organization=p.organization,
        budget=p.budget,
        created_at=p.created_at,
    )


def _auth_response(r) -> AuthResponse:
    return AuthResponse(
        access_token=r.access_token,
        refresh_token=r.refresh_token,
        token_type=r.token_type or "bearer",
        profile=_profile_from_proto(r.profile),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=201,
    summary="Регистрация нового пользователя",
    responses={
        409: {"description": "Email уже зарегистрирован"},
        400: {"description": "Невалидные данные"},
    },
)
async def register(body: RegisterRequest, request: Request) -> AuthResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.Register(auth_pb2.RegisterRequest(
            email=body.email,
            password=body.password,
            name=body.name,
        ))
        return _auth_response(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Вход в систему",
    responses={
        401: {"description": "Неверный email или пароль"},
        404: {"description": "Пользователь не найден"},
    },
)
async def login(body: LoginRequest, request: Request) -> AuthResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.Login(auth_pb2.LoginRequest(
            email=body.email,
            password=body.password,
        ))
        return _auth_response(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Обновление access token",
    responses={
        401: {"description": "refresh_token недействителен или истёк"},
    },
)
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.RefreshToken(auth_pb2.RefreshRequest(
            refresh_token=body.refresh_token,
        ))
        return TokenResponse(
            access_token=resp.access_token,
            refresh_token=resp.refresh_token,
            token_type=resp.token_type or "bearer",
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Профиль текущего пользователя",
    responses={401: {"description": "Не авторизован"}},
)
async def get_me(request: Request) -> UserProfileResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.GetProfile(auth_pb2.GetProfileRequest(
            user_id=request.state.user_id,
        ))
        return _profile_from_proto(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.patch(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Обновление профиля пользователя",
    responses={401: {"description": "Не авторизован"}},
)
async def update_profile(body: UpdateProfileRequest, request: Request) -> UserProfileResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        req = auth_pb2.UpdateProfileRequest(user_id=request.state.user_id)
        if body.client_type is not None:
            req.client_type = body.client_type
        if body.main_task is not None:
            req.main_task = body.main_task
        if body.region is not None:
            req.region = body.region
        if "priority" in body.model_fields_set:
            req.priority_set = True
            req.priority.extend(body.priority)
        if body.risk_tolerance is not None:
            req.risk_tolerance = body.risk_tolerance
        if "preferred_scenarios" in body.model_fields_set:
            req.preferred_scenarios_set = True
            req.preferred_scenarios.extend(body.preferred_scenarios)
        if body.organization is not None:
            req.organization = body.organization
        if body.budget is not None:
            req.budget = body.budget

        resp = await stub.UpdateProfile(req)
        return _profile_from_proto(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)
