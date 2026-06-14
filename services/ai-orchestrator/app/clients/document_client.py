from __future__ import annotations

from google.protobuf.json_format import MessageToDict
import grpc

from app.clients.proto_imports import add_generated_proto_path
from app.config import settings

add_generated_proto_path()

try:
    import document_pb2
    import document_pb2_grpc
except ImportError:
    document_pb2 = None
    document_pb2_grpc = None


class DocumentClient:
    def __init__(self, target: str | None = None) -> None:
        self.target = target or settings.document_grpc
        self.channel_options = [
            ("grpc.max_send_message_length", 128 * 1024 * 1024),
            ("grpc.max_receive_message_length", 128 * 1024 * 1024),
        ]

    async def generate_report(
        self,
        *,
        check_id: str,
        report_json: str,
        format: str = "pdf",
    ) -> dict:
        if document_pb2 is None or document_pb2_grpc is None:
            raise RuntimeError("Generated document proto stubs are missing. Run `make proto` first.")

        request = document_pb2.GenerateReportRequest(
            check_id=check_id,
            report_json=report_json,
            format=format,
        )
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = document_pb2_grpc.DocumentServiceStub(channel)
            response = await stub.GenerateReport(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def extract_text(self, *, document_id: str) -> dict:
        if document_pb2 is None or document_pb2_grpc is None:
            raise RuntimeError("Generated document proto stubs are missing. Run `make proto` first.")

        request = document_pb2.ExtractTextRequest(document_id=document_id)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = document_pb2_grpc.DocumentServiceStub(channel)
            response = await stub.ExtractText(request)
        return MessageToDict(response, preserving_proto_field_name=True)
