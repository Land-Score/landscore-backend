import json
import re
import uuid
from typing import Any

import grpc
from fastapi import APIRouter, Request

router = APIRouter()

_CADASTRAL_RE = re.compile(r"\b\d{2}:\d{2}:\d{5,7}:\d+\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def _alice_response(body: dict[str, Any], text: str, end_session: bool = False) -> dict[str, Any]:
    return {
        "version": body.get("version", "1.0"),
        "session": body.get("session", {}),
        "response": {
            "text": text,
            "tts": text,
            "end_session": end_session,
        },
    }


def _command(body: dict[str, Any]) -> str:
    request = body.get("request") or {}
    return (request.get("original_utterance") or request.get("command") or "").strip()


def _alice_user_uuid(body: dict[str, Any]) -> str:
    session = body.get("session") or {}
    user = session.get("user") or {}
    application = session.get("application") or {}
    source_id = user.get("user_id") or application.get("application_id") or session.get("session_id") or "anonymous"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"landscore-alice:{source_id}"))


def _short_report(report_json: str) -> str:
    try:
        report = json.loads(report_json or "{}")
    except json.JSONDecodeError:
        report = {}
    map_summary = report.get("map_summary") or {}
    area = map_summary.get("parcel_area_ha")
    loss = map_summary.get("loss_percent")
    parts = []
    if area is not None:
        parts.append(f"площадь около {area} гектар")
    if loss is not None:
        parts.append(f"ограничения занимают примерно {loss} процента")
    return ", ".join(parts)


@router.post("/webhook", summary="Webhook для навыка Яндекс Алисы")
async def alice_webhook(body: dict[str, Any], request: Request) -> dict[str, Any]:
    import check_pb2

    command = _command(body).lower()
    if body.get("session", {}).get("new") and not command:
        return _alice_response(
            body,
            "LandScore на связи. Назовите кадастровый номер, и я запущу проверку участка.",
        )

    if any(word in command for word in ("помощь", "что ты умеешь", "команды")):
        return _alice_response(
            body,
            "Я могу запустить проверку по кадастровому номеру, назвать статус проверки по идентификатору или кратко пересказать готовый отчет.",
        )

    check_match = _UUID_RE.search(command)
    if "статус" in command and check_match:
        try:
            status = await request.app.state.check_stub.GetCheckStatus(
                check_pb2.CheckIdRequest(check_id=check_match.group(0))
            )
        except grpc.RpcError:
            return _alice_response(body, "Не смогла найти эту проверку. Проверьте идентификатор.")
        return _alice_response(
            body,
            f"Статус проверки: {status.status}. Текущий шаг: {status.current_step or 'ожидание'}. Прогресс {status.progress_pct} процентов.",
        )

    if ("отчет" in command or "результат" in command) and check_match:
        try:
            report = await request.app.state.check_stub.GetCheckReport(
                check_pb2.CheckIdRequest(check_id=check_match.group(0))
            )
        except grpc.RpcError:
            return _alice_response(body, "Отчет пока не готов или проверка не найдена.")
        details = _short_report(report.report_json)
        score = f"Оценка {report.overall_score} из 100. " if report.overall_score else ""
        risk = f"Правовой риск: {report.legal_risk}. " if report.legal_risk else ""
        tail = f" Детали: {details}." if details else ""
        return _alice_response(body, f"{score}{risk}{report.explanation or 'Отчет готов.'}{tail}")

    cadastral_match = _CADASTRAL_RE.search(command)
    if cadastral_match:
        cadastral_number = cadastral_match.group(0)
        try:
            created = await request.app.state.check_stub.CreateCheck(
                check_pb2.CreateCheckRequest(
                    user_id=_alice_user_uuid(body),
                    cadastral_number=cadastral_number,
                    purpose="Запрос из навыка Алисы",
                    user_profile_json=json.dumps(
                        {
                            "client_type": "private",
                            "main_task": "land_check",
                            "region": "",
                            "risk_tolerance": "medium",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        except grpc.RpcError:
            return _alice_response(body, "Не смогла запустить проверку. Попробуйте еще раз чуть позже.")
        return _alice_response(
            body,
            f"Запустила проверку участка {cadastral_number}. Идентификатор: {created.check_id}. Спросите статус проверки и назовите этот идентификатор.",
        )

    if any(word in command for word in ("стоп", "хватит", "выход")):
        return _alice_response(body, "Хорошо, завершаю.", end_session=True)

    return _alice_response(
        body,
        "Я не нашла кадастровый номер в запросе. Скажите, например: проверь участок 26:11:101101:53.",
    )
