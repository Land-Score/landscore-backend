"""Admin REST API (admin-only).

Mounted at `/api/admin`. Every route is guarded by `require_admin`, which
rejects non-admins with HTTP 403 based on the JWT "role" claim that
AuthMiddleware places on `request.state.role`.

These endpoints aggregate downstream gRPC services:
- auth-service:   ListUsers / UpdateUserRole / SetUserActive / DeleteUser / CountUsers
- check-service:  ListAllChecks / CountChecks
- search-service: ListAllSearches / CountSearches
"""
import asyncio

import grpc
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.deps import require_admin
from app.errors import raise_for_grpc
from app.models import (
    AdminChecksStats,
    AdminCheckItem,
    AdminListChecksResponse,
    AdminListSearchesResponse,
    AdminListUsersResponse,
    AdminSearchesStats,
    AdminSearchItem,
    AdminStatsResponse,
    AdminSuccessResponse,
    AdminUserResponse,
    AdminUsersStats,
    SetUserActiveRequest,
    UpdateUserRoleRequest,
)

# Все маршруты требуют роль администратора.
router = APIRouter(dependencies=[Depends(require_admin)])


def _admin_user(u) -> AdminUserResponse:
    return AdminUserResponse(
        user_id=u.user_id,
        email=u.email,
        name=u.name,
        role=u.role or "user",
        is_active=u.is_active,
        client_type=u.client_type,
        region=u.region,
        created_at=u.created_at,
    )


# ── Сводная статистика ──────────────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Сводная статистика платформы",
    description="Агрегирует счётчики по пользователям, проверкам и поискам.",
)
async def get_stats(request: Request) -> AdminStatsResponse:
    import auth_pb2
    import check_pb2
    import search_pb2

    auth_stub = request.app.state.auth_stub
    check_stub = request.app.state.check_stub
    search_stub = request.app.state.search_stub

    try:
        users, checks, searches = await asyncio.gather(
            auth_stub.CountUsers(auth_pb2.CountUsersRequest()),
            check_stub.CountChecks(check_pb2.CountChecksRequest()),
            search_stub.CountSearches(search_pb2.CountSearchesRequest()),
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)

    return AdminStatsResponse(
        users=AdminUsersStats(
            total=users.total,
            admins=users.admins,
            active=users.active,
        ),
        checks=AdminChecksStats(
            total=checks.total,
            completed=checks.completed,
            processing=checks.processing,
            failed=checks.failed,
            pending=checks.pending,
        ),
        searches=AdminSearchesStats(
            total=searches.total,
            completed=searches.completed,
            processing=searches.processing,
            failed=searches.failed,
            pending=searches.pending,
            awaiting_confirmation=searches.awaiting_confirmation,
        ),
    )


# ── Пользователи ─────────────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=AdminListUsersResponse,
    summary="Список пользователей",
    description="Постраничный список пользователей с опциональным текстовым поиском.",
)
async def list_users(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    query: str = Query("", description="Поиск по email/имени"),
) -> AdminListUsersResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.ListUsers(auth_pb2.ListUsersRequest(
            limit=limit,
            offset=offset,
            query=query,
        ))
        return AdminListUsersResponse(
            users=[_admin_user(u) for u in resp.users],
            total=resp.total,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.patch(
    "/users/{user_id}/role",
    response_model=AdminUserResponse,
    summary="Изменить роль пользователя",
    responses={404: {"description": "Пользователь не найден"}},
)
async def update_user_role(
    user_id: str, body: UpdateUserRoleRequest, request: Request
) -> AdminUserResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.UpdateUserRole(auth_pb2.UpdateUserRoleRequest(
            user_id=user_id,
            role=body.role,
        ))
        return _admin_user(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.patch(
    "/users/{user_id}/active",
    response_model=AdminUserResponse,
    summary="Активировать / заблокировать пользователя",
    responses={404: {"description": "Пользователь не найден"}},
)
async def set_user_active(
    user_id: str, body: SetUserActiveRequest, request: Request
) -> AdminUserResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.SetUserActive(auth_pb2.SetUserActiveRequest(
            user_id=user_id,
            is_active=body.is_active,
        ))
        return _admin_user(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.delete(
    "/users/{user_id}",
    response_model=AdminSuccessResponse,
    summary="Удалить пользователя",
    responses={404: {"description": "Пользователь не найден"}},
)
async def delete_user(user_id: str, request: Request) -> AdminSuccessResponse:
    import auth_pb2
    stub = request.app.state.auth_stub
    try:
        resp = await stub.DeleteUser(auth_pb2.DeleteUserRequest(user_id=user_id))
        return AdminSuccessResponse(success=resp.success)
    except grpc.RpcError as e:
        raise_for_grpc(e)


# ── Проверки ─────────────────────────────────────────────────────────────────

@router.get(
    "/checks",
    response_model=AdminListChecksResponse,
    summary="Все проверки (по всем пользователям)",
    description="Постраничный список проверок с опциональным фильтром по статусу.",
)
async def list_checks(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    status: str = Query("", description="Фильтр по статусу (pending/processing/completed/failed)"),
) -> AdminListChecksResponse:
    import check_pb2
    stub = request.app.state.check_stub
    try:
        resp = await stub.ListAllChecks(check_pb2.ListAllChecksRequest(
            limit=limit,
            offset=offset,
            status=status,
        ))
        return AdminListChecksResponse(
            checks=[
                AdminCheckItem(
                    check_id=c.check_id,
                    user_id=c.user_id,
                    status=c.status,
                    cadastral_number=c.cadastral_number or None,
                    address=c.address or None,
                    created_at=c.created_at,
                    completed_at=c.completed_at or None,
                )
                for c in resp.checks
            ],
            total=resp.total,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.delete(
    "/checks/{check_id}",
    response_model=AdminSuccessResponse,
    summary="Удалить проверку",
)
async def delete_check(check_id: str, request: Request) -> AdminSuccessResponse:
    import check_pb2
    stub = request.app.state.check_stub
    try:
        resp = await stub.DeleteCheck(check_pb2.CheckIdRequest(check_id=check_id))
        return AdminSuccessResponse(success=resp.success)
    except grpc.RpcError as e:
        raise_for_grpc(e)


# ── Поиски ───────────────────────────────────────────────────────────────────

@router.get(
    "/searches",
    response_model=AdminListSearchesResponse,
    summary="Все поиски (по всем пользователям)",
    description="Постраничный список поисков по всем пользователям.",
)
async def list_searches(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
) -> AdminListSearchesResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.ListAllSearches(search_pb2.ListAllSearchesRequest(
            limit=limit,
            offset=offset,
        ))
        return AdminListSearchesResponse(
            searches=[
                AdminSearchItem(
                    search_id=s.search_id,
                    user_id=s.user_id,
                    status=s.status,
                    query=s.query,
                    created_at=s.created_at,
                )
                for s in resp.searches
            ],
            total=resp.total,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)
