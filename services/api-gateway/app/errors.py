import grpc
from fastapi import HTTPException

_GRPC_TO_HTTP: dict[grpc.StatusCode, int] = {
    grpc.StatusCode.OK: 200,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.UNAUTHENTICATED: 401,
    grpc.StatusCode.PERMISSION_DENIED: 403,
    grpc.StatusCode.UNIMPLEMENTED: 501,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.INTERNAL: 500,
    grpc.StatusCode.RESOURCE_EXHAUSTED: 429,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    grpc.StatusCode.CANCELLED: 499,
}


def grpc_to_http(err: grpc.RpcError) -> HTTPException:
    code = err.code()
    http_status = _GRPC_TO_HTTP.get(code, 500)
    detail = err.details() or code.name
    return HTTPException(status_code=http_status, detail=detail)


def raise_for_grpc(err: grpc.RpcError) -> None:
    raise grpc_to_http(err)
