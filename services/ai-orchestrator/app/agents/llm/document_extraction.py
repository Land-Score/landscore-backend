import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

DOCUMENT_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "documents_present": {"type": "boolean"},
        "cadastral_number": {"type": ["string", "null"]},
        "area_cadastral_sqm": {"type": ["number", "null"]},
        "area_actual_sqm": {"type": ["number", "null"]},
        "category": {"type": ["string", "null"]},
        "allowed_use": {"type": ["string", "null"]},
        "owner": {"type": ["string", "null"]},
        "ownership_form": {"type": ["string", "null"]},
        "cadastral_price": {"type": ["number", "null"]},
        "cadastral_price_date": {"type": ["string", "null"]},
        "encumbrances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                },
            },
        },
        "inconsistencies": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["documents_present", "summary"],
}


class DocumentExtractionAgent(BaseLLMAgent):
    name = "DocumentExtractionAgent"
    prompt_file = "document_extraction.txt"
    response_schema = DOCUMENT_EXTRACTION_SCHEMA
    temperature = 0.1

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Document agent sees only the uploaded document texts.
        documents = ctx.plot.extracted_documents or ctx.get("extracted_documents") or []
        has_documents = bool(documents)
        return json.dumps({
            "documents_present": has_documents,
            "documents": documents,
            "known_cadastral_number": ctx.plot.cadastral_number or None,
            "rule": (
                "Извлекай только то, что есть в текстах документов; не домысливай. "
                "Если документов нет (documents пустой), верни documents_present=false, "
                "пустые поля и summary: 'Документы не предоставлены, извлечение не выполнялось'."
            ),
        }, ensure_ascii=False, default=str)
