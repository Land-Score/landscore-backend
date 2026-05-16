import grpc


class LandScoreError(Exception):
    grpc_status = grpc.StatusCode.INTERNAL

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(LandScoreError):
    grpc_status = grpc.StatusCode.NOT_FOUND


class ValidationError(LandScoreError):
    grpc_status = grpc.StatusCode.INVALID_ARGUMENT


class UnauthorizedError(LandScoreError):
    grpc_status = grpc.StatusCode.UNAUTHENTICATED


def handle_grpc_error(error: LandScoreError, context: grpc.ServicerContext) -> None:
    context.set_code(error.grpc_status)
    context.set_details(error.message)
