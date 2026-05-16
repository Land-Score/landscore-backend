from fastapi import APIRouter, Request, UploadFile, File

router = APIRouter()


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...)):
    # TODO: call document-service via gRPC
    return {"document_id": "stub", "filename": file.filename}
