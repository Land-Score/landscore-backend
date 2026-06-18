import grpc
from fastapi import APIRouter, Request, UploadFile, File, HTTPException

from app.errors import raise_for_grpc
from app.models import DocumentUploadResponse, DocumentResponse

router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_UPLOAD_CHUNK_BYTES = 1024 * 1024  # читаем тело потоково по 1 МБ
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
}


async def _read_capped(file: UploadFile, request: Request) -> bytes:
    """Читает тело файла потоково, прерываясь, как только превышен лимит.

    Проверяет заголовок Content-Length до буферизации, затем дочитывает чанками
    и обрывает чтение при превышении лимита — чтобы не буферизовать гигантское
    тело в памяти (защита от DoS через загрузку).
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail="Файл превышает лимит 50 МБ")
        except ValueError:
            pass  # некорректный заголовок — положимся на проверку по чанкам

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail="Файл превышает лимит 50 МБ")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=201,
    summary="Загрузить документ",
    description=(
        "Загружает PDF или изображение (JPEG, PNG, TIFF) для последующего анализа. "
        "Максимальный размер файла: 50 МБ. "
        "Возвращает `document_id` для передачи в `POST /api/checks`."
    ),
    responses={
        400: {"description": "Недопустимый тип файла или превышен размер"},
        413: {"description": "Файл слишком большой"},
    },
)
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="PDF или изображение участка/документа"),
) -> DocumentUploadResponse:
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            400,
            detail=f"Тип файла '{file.content_type}' не поддерживается. "
                   f"Допустимые: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}",
        )

    content = await _read_capped(file, request)

    import document_pb2
    stub = request.app.state.document_stub
    try:
        resp = await stub.StoreDocument(document_pb2.StoreDocumentRequest(
            user_id=request.state.user_id,
            filename=file.filename or "document",
            content_type=file.content_type or "application/octet-stream",
            data=content,
        ))
        return DocumentUploadResponse(
            document_id=resp.document_id,
            filename=file.filename or "document",
            size_bytes=len(content),
            content_type=file.content_type or "",
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Получить документ",
    description="Возвращает метаданные и presigned URL для скачивания (действует 24 часа).",
    responses={404: {"description": "Документ не найден"}},
)
async def get_document(document_id: str, request: Request) -> DocumentResponse:
    import document_pb2
    stub = request.app.state.document_stub
    try:
        resp = await stub.GetDocument(document_pb2.GetDocumentRequest(
            document_id=document_id,
        ))
    except grpc.RpcError as e:
        raise_for_grpc(e)

    # Проверка владения: чужой документ выглядит как несуществующий (404),
    # чтобы не раскрывать факт его существования другому пользователю.
    if resp.user_id != request.state.user_id:
        raise HTTPException(404, detail="Документ не найден")

    return DocumentResponse(
        document_id=resp.document_id,
        filename=resp.filename,
        size_bytes=resp.size_bytes,
        download_url=resp.download_url,
    )


@router.delete(
    "/{document_id}",
    status_code=204,
    summary="Удалить документ",
    responses={
        204: {"description": "Документ удалён"},
        404: {"description": "Документ не найден"},
    },
)
async def delete_document(document_id: str, request: Request):
    import document_pb2
    stub = request.app.state.document_stub

    # Сначала проверяем владение — иначе любой пользователь мог бы удалить
    # чужой документ. Чужой/несуществующий документ -> 404.
    try:
        doc = await stub.GetDocument(document_pb2.GetDocumentRequest(
            document_id=document_id,
        ))
    except grpc.RpcError as e:
        raise_for_grpc(e)

    if doc.user_id != request.state.user_id:
        raise HTTPException(404, detail="Документ не найден")

    try:
        await stub.DeleteDocument(document_pb2.DeleteDocumentRequest(
            document_id=document_id,
        ))
    except grpc.RpcError as e:
        raise_for_grpc(e)
