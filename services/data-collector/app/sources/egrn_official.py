"""
Paid ЕГРН (Level-1) integration via commercial Rosreestr aggregators.

Two providers are supported (EGRN_MODE selects):
  * newdb  — newdb.net, ASYNC: POST {base} + poll {base}/data?requestId&token.
             Key: EGRN_API_KEY. ~100 req/mo. Only the POST consumes balance.
  * parser — parser-api.com, SYNC: GET {base}?key=&cadNumber=.
             Key: EGRN_PARSER_KEY. ~200 req/mo. Returns {success, records:[...]}.
  * auto   — try parser first (more quota, synchronous), fall back to newdb ONLY
             on failure (a successful-but-empty result is returned as-is, so we
             never spend the fallback provider's quota needlessly).
  * mock / off — deterministic placeholder / disabled.

Owner ФИО of physical persons is NOT returned (266-ФЗ closed it since 2023-03-01);
both providers only expose the ownership FORM (e.g. "Частная"). ``collect()``
always returns a normalized dict and never raises.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from app.config import settings

NEWDB_DEFAULT_BASE = "https://api.newdb.net/v2"
PARSER_DEFAULT_BASE = "https://parser-api.com/parser/egrn_api/details_by_number"

_POLL_INTERVAL_S = 6.0
_POLL_MAX_WAIT_S = 90.0

# Encumbrance type substrings that are stop-factor / high-risk for a deal.
_ENCUMBRANCE_RISK = {
    "арест", "запрещение", "запрет", "ипотека", "залог",
    "аренда", "сервитут", "доверительное", "концесси",
}


class EGRNOfficialClient:
    """Client for paid ЕГРН Level-1 aggregators (newdb + parser-api), with auto-fallback."""

    def __init__(self, mode: str | None = None, timeout: float | None = None) -> None:
        self.mode = (mode or settings.egrn_mode or "off").lower()
        self.timeout = timeout or settings.egrn_timeout
        self.newdb_key = settings.egrn_api_key
        self.newdb_base = (settings.egrn_api_url or NEWDB_DEFAULT_BASE).rstrip("/")
        self.parser_key = settings.egrn_parser_key
        self.parser_base = (settings.egrn_parser_url or PARSER_DEFAULT_BASE).rstrip("/")

    def _chain(self) -> list[str]:
        if self.mode == "auto":
            return ["parser", "newdb"]
        if self.mode in ("newdb", "parser"):
            return [self.mode]
        return []

    def _key_for(self, provider: str) -> str:
        return self.parser_key if provider == "parser" else self.newdb_key

    async def collect(self, *, cadastral_number: str) -> dict[str, Any]:
        started = time.monotonic()
        cadastral_number = (cadastral_number or "").strip()

        if self.mode in ("off", ""):
            return self._empty(cadastral_number, started, warning="egrn_official_disabled_set_EGRN_MODE_to_enable")
        if not cadastral_number:
            return self._empty(cadastral_number, started, warning="egrn_official_skipped_missing_cadastral_number")
        if self.mode == "mock":
            return self._mock(cadastral_number, started)

        chain = self._chain()
        if not chain:
            return self._empty(cadastral_number, started, warning=f"egrn_official_unknown_mode:{self.mode}")

        warnings: list[str] = []
        for provider in chain:
            if not self._key_for(provider):
                warnings.append(f"egrn_official_skipped_{provider}_missing_api_key")
                continue
            try:
                result = await self._collect_provider(provider, cadastral_number, started)
                # Success (incl. legitimately empty) — return; only fall through on error.
                result["warnings"] = warnings + result.get("warnings", [])
                return result
            except Exception as exc:  # try the next provider in the chain
                warnings.append(f"egrn_official_{provider}_failed:{exc}")
                continue

        return self._empty(cadastral_number, started, warning="; ".join(warnings) or "egrn_official_no_provider", success=False)

    async def _collect_provider(self, provider: str, cadastral_number: str, started: float) -> dict[str, Any]:
        if provider == "newdb":
            payload, balance = await self._request_newdb(cadastral_number)
            normalized = self._normalize_newdb(payload)
        else:  # parser
            payload, balance = await self._request_parser(cadastral_number)
            normalized = self._normalize_parser(payload)

        warnings: list[str] = []
        if not normalized["rights"] and not normalized["encumbrances"]:
            warnings.append("egrn_official_no_rights_or_encumbrances_returned")
        return {
            "success": True,
            "source": f"egrn_official:{provider}",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number,
            "balance": balance,
            **normalized,
            "raw": payload,
            "diagnostics": {"ok": True, "provider": provider, "balance": balance},
            "warnings": warnings,
            "limitations": [
                "physical_person_owner_names_are_closed_by_266-FZ_and_not_returned",
                "data_is_an_aggregator_copy_not_a_directly_certified_rosreestr_extract",
            ],
        }

    # ── newdb (async POST + poll) ──────────────────────────────────────────────
    async def _request_newdb(self, cadastral_number: str) -> tuple[dict[str, Any], int | None]:
        request_id = str(uuid.uuid4())
        body = {"params": {"address": cadastral_number, "method": "rosreestr", "country": "ru"}, "requestId": request_id}
        headers = {"X-API-KEY": self.newdb_key, "Content-Type": "application/json", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.newdb_base, json=body, headers=headers)
            resp.raise_for_status()
            first = resp.json()
            balance = first.get("balance")
            if first.get("state") == "complete" and self._extract_newdb_record(first):
                return first, balance
            deadline = time.monotonic() + _POLL_MAX_WAIT_S
            poll_url = f"{self.newdb_base}/data"
            while time.monotonic() < deadline:
                await asyncio.sleep(_POLL_INTERVAL_S)
                poll = await client.get(poll_url, params={"requestId": request_id, "token": self.newdb_key},
                                        headers={"Accept": "application/json"})
                poll.raise_for_status()
                data = poll.json()
                balance = data.get("balance", balance)
                state = data.get("state")
                if state == "complete":
                    return data, balance
                if state in ("timeout", "error", "failed"):
                    raise RuntimeError(f"newdb state={state}")
            raise RuntimeError(f"newdb no result within {_POLL_MAX_WAIT_S:.0f}s")

    @staticmethod
    def _extract_newdb_record(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            data = payload["results"]["rosreestr"]["result"]["data"]
        except (KeyError, TypeError):
            return {}
        return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}

    def _normalize_newdb(self, payload: dict[str, Any]) -> dict[str, Any]:
        rec = self._extract_newdb_record(payload)
        rights = [
            {"type": str(r.get("rightTypeDesc") or r.get("rightType") or "").strip(),
             "date": str(r.get("rightRegDate") or "").strip(),
             "number": str(r.get("rightNumber") or "").strip(),
             "share": str(r.get("part") or "").strip()}
            for r in (rec.get("rights") or []) if isinstance(r, dict)
        ]
        encumbrances = []
        for e in rec.get("encumbrances") or []:
            if not isinstance(e, dict):
                continue
            td = str(e.get("typeDesc") or e.get("type") or "").strip()
            encumbrances.append({"type": td, "number": str(e.get("encumbranceNumber") or "").strip(),
                                 "date": str(e.get("startDate") or e.get("encumbranceDate") or "").strip(),
                                 "is_risk": _is_risk(td)})
        addr = rec.get("address") or {}
        return {
            "rights": rights, "encumbrances": encumbrances,
            "owner_type": str(rec.get("ownershipType") or "").strip(), "owner_name": None,
            "registration_date": str(rec.get("regDate") or "").strip(),
            "cadastral_cost": str(rec.get("cadCost") or "").strip(),
            "land_category": str(rec.get("landCategory") or "").strip(),
            "permitted_use": str(rec.get("permittedUseByDoc") or rec.get("permittedUse") or "").strip(),
            "readable_address": str(addr.get("readableAddress") or "").strip() if isinstance(addr, dict) else "",
        }

    # ── parser-api (synchronous GET) ───────────────────────────────────────────
    async def _request_parser(self, cadastral_number: str) -> tuple[dict[str, Any], int | None]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(self.parser_base, params={"key": self.parser_key, "cadNumber": cadastral_number},
                                    headers={"Accept": "application/json"})
            resp.raise_for_status()
            payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(f"parser success={payload.get('success')}")
        return payload, None  # parser-api does not return a per-request balance

    def _normalize_parser(self, payload: dict[str, Any]) -> dict[str, Any]:
        records = payload.get("records") or []
        rec = records[0] if records and isinstance(records[0], dict) else {}
        rights = [
            {"type": str(r.get("type") or "").strip(), "date": str(r.get("date") or "").strip(),
             "number": str(r.get("number") or "").strip(), "share": str(r.get("share") or "").strip()}
            for r in (rec.get("rights") or []) if isinstance(r, dict)
        ]
        encumbrances = []
        for e in rec.get("encumbrances") or []:
            if isinstance(e, str):
                encumbrances.append({"type": e, "number": "", "date": "", "is_risk": _is_risk(e)})
                continue
            if not isinstance(e, dict):
                continue
            td = str(e.get("type") or e.get("typeDesc") or e.get("name") or "").strip()
            encumbrances.append({"type": td, "number": str(e.get("number") or e.get("reg_number") or "").strip(),
                                 "date": str(e.get("date") or e.get("start_date") or "").strip(),
                                 "is_risk": _is_risk(td)})
        return {
            "rights": rights, "encumbrances": encumbrances,
            "owner_type": str(rec.get("ownership") or "").strip(), "owner_name": None,
            "registration_date": str(rec.get("reg_date") or "").strip(),
            "cadastral_cost": str(rec.get("cad_cost") or "").strip(),
            "land_category": str(rec.get("land_category") or rec.get("category") or "").strip(),
            "permitted_use": str(rec.get("permitted_use") or rec.get("purpose") or "").strip(),
            "readable_address": str(rec.get("address") or "").strip(),
        }

    def _mock(self, cadastral_number: str, started: float) -> dict[str, Any]:
        return {
            "success": True, "source": "egrn_official:mock", "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number, "balance": None,
            "rights": [{"type": "Собственность", "date": "01.03.2013", "number": "50-50-20/020/2013-320", "share": ""}],
            "encumbrances": [{"type": "Ипотека в силу закона", "number": "50-50-20/020/2013-322", "date": "01.03.2013", "is_risk": True}],
            "owner_type": "Частная", "owner_name": None, "registration_date": "09.04.2014",
            "cadastral_cost": "5133262.85", "land_category": "", "permitted_use": "", "readable_address": "",
            "raw": {"mock": True, "cadastral_number": cadastral_number},
            "diagnostics": {"ok": True, "provider": "mock"},
            "warnings": ["egrn_official_mock_data_for_wiring_only"],
            "limitations": ["mock_data_not_a_real_egrn_extract"],
        }

    def _empty(self, cadastral_number: str, started: float, *, warning: str, success: bool = True) -> dict[str, Any]:
        return {
            "success": success, "source": f"egrn_official:{self.mode}", "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number, "balance": None,
            "rights": [], "encumbrances": [], "owner_type": "", "owner_name": None,
            "registration_date": "", "cadastral_cost": "", "land_category": "", "permitted_use": "", "readable_address": "",
            "raw": {}, "diagnostics": {"ok": success, "mode": self.mode, "warning": warning},
            "warnings": [warning], "limitations": [],
        }


def _is_risk(type_desc: str) -> bool:
    low = (type_desc or "").lower()
    return any(m in low for m in _ENCUMBRANCE_RISK)
