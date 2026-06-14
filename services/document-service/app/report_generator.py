"""Render a check report (report_json) into a B2B HTML template and PDF.

The report_json string is the ``report_payload`` produced by
ai-orchestrator's ``_save_check_result`` (see services/ai-orchestrator/app/tasks.py).
Shape (all keys optional / defensively accessed):

    {
      "check_id", "plot", "nspd", "area_summary",
      "chief_decision": {overall_score, legal_risk, best_scenario, ...},
      "critical_risk": {stop_factors: [...], ...},
      "report": {...}, "client_explanation": {...},
      "next_steps": {steps: [...] | [...]},
      ...
    }

WeasyPrint converts the HTML to PDF. If WeasyPrint's native libraries
(pango/cairo) are missing, the renderer degrades by storing the raw HTML and
notes the fallback in the returned content type.
"""

from __future__ import annotations

import html
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.minio_client import store


@dataclass
class ReportResult:
    report_id: str
    download_url: str
    storage_path: str
    content_type: str
    degraded: bool
    note: str


# ---- small helpers --------------------------------------------------------
def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _stop_factors(critical: dict[str, Any]) -> list[str]:
    raw = critical.get("stop_factors") or critical.get("stopFactors") or []
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(str(_coalesce(item.get("title"), item.get("name"), item.get("description"), item)))
            elif item not in (None, ""):
                out.append(str(item))
    elif raw:
        out.append(str(raw))
    return out


def _next_steps(next_steps: Any) -> list[str]:
    if isinstance(next_steps, dict):
        raw = next_steps.get("steps") or next_steps.get("next_steps") or next_steps.get("items") or []
    elif isinstance(next_steps, list):
        raw = next_steps
    else:
        raw = []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(str(_coalesce(item.get("action"), item.get("title"), item.get("text"), item.get("description"), item)))
        elif item not in (None, ""):
            out.append(str(item))
    return out


def _score_class(score: Any) -> str:
    try:
        value = int(score)
    except (TypeError, ValueError):
        return "neutral"
    if value >= 70:
        return "ok"
    if value >= 45:
        return "warn"
    return "critical"


def _rows(pairs: list[tuple[str, Any]]) -> str:
    cells = []
    for label, value in pairs:
        if value in (None, "", [], {}):
            continue
        cells.append(
            f'<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>'
        )
    return "".join(cells)


# ---- HTML template --------------------------------------------------------
_CSS = """
@page { size: A4; margin: 22mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: "DejaVu Sans", "Arial", sans-serif;
  color: #1e293b; font-size: 12px; line-height: 1.5; margin: 0;
}
h1 { font-size: 22px; color: #1e3a8a; margin: 0 0 4px; }
h2 { font-size: 15px; color: #1e40af; margin: 22px 0 8px;
     border-bottom: 2px solid #dbeafe; padding-bottom: 4px; }
.subtitle { color: #64748b; font-size: 11px; margin: 0 0 18px; }
.scorecard { display: flex; gap: 14px; margin: 16px 0 4px; }
.score-box { border-radius: 10px; padding: 14px 18px; color: #fff; min-width: 150px; }
.score-box .label { font-size: 10px; text-transform: uppercase; opacity: .9; letter-spacing: .04em; }
.score-box .value { font-size: 26px; font-weight: 700; line-height: 1.1; }
.ok { background: #15803d; }
.warn { background: #b45309; }
.critical { background: #b91c1c; }
.neutral { background: #475569; }
table { width: 100%; border-collapse: collapse; margin: 6px 0; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid #e2e8f0; }
th { width: 38%; color: #475569; font-weight: 600; background: #f1f5f9; }
ul { margin: 6px 0; padding-left: 18px; }
li { margin: 3px 0; }
.callout { background: #fef2f2; border-left: 4px solid #b91c1c; padding: 10px 14px;
           border-radius: 6px; margin: 8px 0; color: #7f1d1d; }
.callout.none { background: #f0fdf4; border-left-color: #15803d; color: #14532d; }
.verdict { background: #eff6ff; border-left: 4px solid #1e40af; padding: 12px 16px;
           border-radius: 6px; margin: 8px 0; font-size: 13px; }
.footer { margin-top: 28px; padding-top: 10px; border-top: 1px solid #e2e8f0;
          color: #94a3b8; font-size: 10px; }
"""


def _render_html(payload: dict[str, Any]) -> str:
    check_id = _coalesce(payload.get("check_id"), "—")
    plot = _as_dict(payload.get("plot"))
    nspd = _as_dict(payload.get("nspd"))
    area = _as_dict(payload.get("area_summary"))
    decision = _as_dict(payload.get("chief_decision"))
    critical = _as_dict(payload.get("critical_risk"))
    explanation = _as_dict(payload.get("client_explanation"))

    overall_score = _coalesce(decision.get("overall_score"), decision.get("overallScore"))
    legal_risk = _coalesce(decision.get("legal_risk"), decision.get("legalRisk"))
    best_scenario = _coalesce(decision.get("best_scenario"), decision.get("bestScenario"))
    verdict_text = _coalesce(
        decision.get("recommendation"),
        decision.get("verdict"),
        decision.get("summary"),
        explanation.get("text"),
        explanation.get("summary"),
    )

    stop_factors = _stop_factors(critical)
    steps = _next_steps(payload.get("next_steps"))

    cadastral = _coalesce(nspd.get("cadastral_number"), plot.get("cadastral_number"))
    address = _coalesce(nspd.get("address"), plot.get("address"))

    plot_rows = _rows([
        ("Кадастровый номер", cadastral),
        ("Адрес", address),
        ("Категория земель", _coalesce(nspd.get("category"), plot.get("category"))),
        ("Вид разрешённого использования", _coalesce(nspd.get("allowed_use"), plot.get("allowed_use"))),
        ("Форма собственности", _coalesce(nspd.get("owner_type"), plot.get("owner_type"))),
        ("Кадастровая стоимость", _coalesce(nspd.get("cadastral_price"), plot.get("price"))),
        ("Статус (НСПД)", nspd.get("status")),
    ])

    area_rows = _rows([
        ("Кадастровая площадь, га", area.get("cadastral_area_ha")),
        ("Площадь по геометрии, га", area.get("geometry_area_ha")),
        ("Площадь в ограничениях, га", area.get("restricted_area_ha")),
        ("Полезная площадь, га", area.get("usable_area_ha")),
        ("Потери площади, %", area.get("loss_percent")),
    ])

    score_class = _score_class(overall_score)
    score_display = f"{_esc(overall_score)}/100" if overall_score not in (None, "") else "—"
    sf_count = len(stop_factors)
    sf_class = "critical" if sf_count else "ok"

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Verdict
    verdict_html = (
        f'<div class="verdict"><strong>Итог:</strong> {_esc(verdict_text)}</div>'
        if verdict_text else ""
    )

    # Stop factors
    if stop_factors:
        sf_items = "".join(f"<li>{_esc(s)}</li>" for s in stop_factors)
        stop_html = (
            f'<div class="callout"><strong>Стоп-факторы: {sf_count}</strong>'
            f'<ul>{sf_items}</ul></div>'
        )
    else:
        stop_html = '<div class="callout none"><strong>Стоп-факторы не выявлены.</strong></div>'

    steps_html = ""
    if steps:
        items = "".join(f"<li>{_esc(s)}</li>" for s in steps)
        steps_html = f"<h2>Следующие шаги</h2><ul>{items}</ul>"

    area_block = f"<h2>Площадь и ограничения</h2><table>{area_rows}</table>" if area_rows else ""

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
  <h1>Отчёт по проверке земельного участка</h1>
  <p class="subtitle">Проверка #{_esc(check_id)} · сформировано LandScore AI · {_esc(generated_at)}</p>

  <div class="scorecard">
    <div class="score-box {score_class}">
      <div class="label">Общий рейтинг</div>
      <div class="value">{score_display}</div>
    </div>
    <div class="score-box {sf_class}">
      <div class="label">Стоп-факторы</div>
      <div class="value">{sf_count}</div>
    </div>
  </div>

  {verdict_html}
  {stop_html}

  <h2>Параметры участка</h2>
  <table>{plot_rows}</table>

  {area_block}

  <h2>Заключение</h2>
  <table>{_rows([
      ("Правовой риск", legal_risk),
      ("Лучший сценарий использования", best_scenario),
  ])}</table>

  {steps_html}

  <div class="footer">
    Документ сформирован автоматически платформой LandScore AI и носит
    информационный характер. Перед сделкой требуется проверка актуальной выписки ЕГРН.
  </div>
</body></html>"""


def _html_to_pdf(html_str: str) -> bytes:
    """Render HTML to PDF bytes via WeasyPrint. Raises if native libs missing."""
    from weasyprint import HTML

    return HTML(string=html_str).write_pdf()


def generate_report(*, check_id: str, report_json: str, fmt: str = "pdf") -> ReportResult:
    try:
        payload = json.loads(report_json) if report_json else {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except json.JSONDecodeError:
        payload = {"check_id": check_id, "error": "report_json is not valid JSON"}

    payload.setdefault("check_id", check_id)
    html_str = _render_html(payload)

    report_uuid = uuid.uuid4().hex
    fmt = (fmt or "pdf").lower()

    if fmt == "html":
        report_id = f"{report_uuid}.html"
        path = store.store_report(report_id=report_id, data=html_str.encode("utf-8"), content_type="text/html; charset=utf-8")
        url = store.presigned_report_url(report_id)
        return ReportResult(report_id, url, path, "text/html", False, "html_requested")

    # Default: PDF, with graceful degradation to HTML on missing native libs.
    try:
        pdf_bytes = _html_to_pdf(html_str)
        report_id = f"{report_uuid}.pdf"
        path = store.store_report(report_id=report_id, data=pdf_bytes, content_type="application/pdf")
        url = store.presigned_report_url(report_id)
        return ReportResult(report_id, url, path, "application/pdf", False, "")
    except Exception as exc:  # WeasyPrint native libs missing or render error.
        report_id = f"{report_uuid}.html"
        path = store.store_report(
            report_id=report_id,
            data=html_str.encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
        url = store.presigned_report_url(report_id)
        return ReportResult(
            report_id,
            url,
            path,
            "text/html",
            True,
            f"pdf_unavailable: WeasyPrint failed ({exc}); stored HTML instead",
        )
