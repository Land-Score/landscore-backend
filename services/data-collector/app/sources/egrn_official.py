"""
Paid ЕГРН (Level-1) integration via the newdb.net Rosreestr aggregator.

Unlike the public NSPD reference data (see :mod:`app.rosreestr_client`), this
source backs an official EGRN extract: rights, encumbrances (ипотека/аресты),
registration dates, cadastral cost. Owner ФИО of physical persons is NOT
returned (266-ФЗ closed it since 2023-03-01) and newdb does not expose it.

newdb.net is an ASYNCHRONOUS API:
  1. POST {base}            {params:{address, method:"rosreestr", country:"ru"}, requestId}
                            -> {requestId, state:"queued", balance}
  2. GET  {base}/data?requestId=...&token=...
                            -> {state:"complete"|"queued"|"timeout", results:{rosreestr:{result:{data:[...]}}}}
  Only the POST consumes balance; polling the result is free.

Gated behind ``EGRN_MODE``: off (default) | mock | newdb | parser.
``collect()`` always returns a normalized dict and never raises.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from app.config import settings

NEWDB_DEFAULT_BASE = "https://api.newdb.net/v2"
PARSER_DEFAULT_BASE = "https://parser-api.com/api/egrn"

# How long to wait for an async newdb result before giving up (seconds), and the
# poll interval. newdb typically completes a rosreestr task in ~40s.
_POLL_INTERVAL_S = 6.0
_POLL_MAX_WAIT_S = 90.0

# Encumbrance type codes that are stop-factor / high-risk for a deal.
_ENCUMBRANCE_RISK = {
    "арест",
    "запрещение",
    "запрет",
    "ипотека",
    "залог",
    "аренда",
    "сервитут",
    "доверительное",
    "концесси",
}


class EGRNOfficialClient:
    """Async client for the newdb.net paid ЕГРН aggregator."""

    def __init__(
        self,
        mode: str | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.mode = (mode or settings.egrn_mode or "off").lower()
        self.api_key = api_key if api_key is not None else settings.egrn_api_key
        self.timeout = timeout or settings.egrn_timeout
        self.base_url = ((api_url if api_url is not None else settings.egrn_api_url) or self._default_base()).rstrip("/")

    def _default_base(self) -> str:
        return PARSER_DEFAULT_BASE if self.mode == "parser" else NEWDB_DEFAULT_BASE

    async def collect(self, *, cadastral_number: str) -> dict[str, Any]:
        started = time.monotonic()
        cadastral_number = (cadastral_number or "").strip()

        if self.mode in ("off", ""):
            return self._empty(cadastral_number, started, warning="egrn_official_disabled_set_EGRN_MODE_to_enable")
        if not cadastral_number:
            return self._empty(cadastral_number, started, warning="egrn_official_skipped_missing_cadastral_number")
        if self.mode == "mock":
            return self._mock(cadastral_number, started)
        if self.mode not in ("newdb", "parser"):
            return self._empty(cadastral_number, started, warning=f"egrn_official_unknown_mode:{self.mode}")
        if not self.api_key:
            return self._empty(cadastral_number, started, warning="egrn_official_skipped_missing_api_key")

        try:
            if self.mode == "newdb":
                payload, balance = await self._request_newdb(cadastral_number)
            else:
                payload, balance = await self._request_parser(cadastral_number)
        except _EgrnTimeout as exc:
            return self._empty(cadastral_number, started, warning=f"egrn_official_timeout:{exc}", success=False)
        except httpx.HTTPError as exc:
            return self._empty(cadastral_number, started, warning=f"egrn_official_request_failed:{exc}", success=False)
        except Exception as exc:  # never let an aggregator quirk break the pipeline
            return self._empty(cadastral_number, started, warning=f"egrn_official_unexpected_error:{exc}", success=False)

        normalized = self._normalize(payload)
        warnings: list[str] = []
        if not normalized["rights"] and not normalized["encumbrances"]:
            warnings.append("egrn_official_no_rights_or_encumbrances_returned")
        return {
            "success": True,
            "source": f"egrn_official:{self.mode}",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number,
            "balance": balance,
            **normalized,
            "raw": payload,
            "diagnostics": {"ok": True, "mode": self.mode, "url": self.base_url, "balance": balance},
            "warnings": warnings,
            "limitations": [
                "physical_person_owner_names_are_closed_by_266-FZ_and_not_returned",
                "data_is_an_aggregator_copy_not_a_directly_certified_rosreestr_extract",
            ],
        }

    # ── newdb async POST + poll ────────────────────────────────────────────────
    async def _request_newdb(self, cadastral_number: str) -> tuple[dict[str, Any], int | None]:
        request_id = str(uuid.uuid4())
        body = {
            "params": {"address": cadastral_number, "method": "rosreestr", "country": "ru"},
            "requestId": request_id,
        }
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, json=body, headers=headers)
            resp.raise_for_status()
            first = resp.json()
            balance = first.get("balance")
            # Fast path: some cached lookups may come back complete immediately.
            if first.get("state") == "complete" and self._extract_rosreestr(first):
                return first, balance

            # Poll the (free) result endpoint until complete/timeout.
            deadline = time.monotonic() + _POLL_MAX_WAIT_S
            poll_url = f"{self.base_url}/data"
            while time.monotonic() < deadline:
                await asyncio.sleep(_POLL_INTERVAL_S)
                poll = await client.get(poll_url, params={"requestId": request_id, "token": self.api_key},
                                        headers={"Accept": "application/json"})
                poll.raise_for_status()
                data = poll.json()
                balance = data.get("balance", balance)
                state = data.get("state")
                if state == "complete":
                    return data, balance
                if state in ("timeout", "error", "failed"):
                    raise _EgrnTimeout(f"state={state}")
            raise _EgrnTimeout(f"no result within {_POLL_MAX_WAIT_S:.0f}s")

    async def _request_parser(self, cadastral_number: str) -> tuple[dict[str, Any], int | None]:
        # Best-effort fallback (parser-api.com); shape unverified, kept defensive.
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(self.base_url, params={"key": self.api_key, "cadastral_number": cadastral_number},
                                    headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json(), None

    # ── normalization ──────────────────────────────────────────────────────────
    @staticmethod
    def _extract_rosreestr(payload: dict[str, Any]) -> dict[str, Any]:
        """Pull the first plot record out of a newdb rosreestr response."""
        try:
            data = payload["results"]["rosreestr"]["result"]["data"]
        except (KeyError, TypeError):
            # parser-api / alternative shapes
            data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], dict) else {}
        if isinstance(data, dict):
            return data
        return {}

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        rec = self._extract_rosreestr(payload)

        rights = []
        for r in rec.get("rights") or []:
            if not isinstance(r, dict):
                continue
            rights.append({
                "type": str(r.get("rightTypeDesc") or r.get("rightType") or "").strip(),
                "date": str(r.get("rightRegDate") or "").strip(),
                "number": str(r.get("rightNumber") or "").strip(),
                "share": str(r.get("part") or "").strip(),
            })

        encumbrances = []
        for e in rec.get("encumbrances") or []:
            if isinstance(e, str):
                encumbrances.append({"type": e, "number": "", "date": "", "is_risk": True})
                continue
            if not isinstance(e, dict):
                continue
            type_desc = str(e.get("typeDesc") or e.get("type") or "").strip()
            encumbrances.append({
                "type": type_desc,
                "number": str(e.get("encumbranceNumber") or "").strip(),
                "date": str(e.get("startDate") or e.get("encumbranceDate") or "").strip(),
                "is_risk": any(m in type_desc.lower() for m in _ENCUMBRANCE_RISK),
            })

        # 266-ФЗ: ФИО физлица не возвращается. owner_name остаётся None;
        # тип владения берём из ownershipType, если он есть (обычно ЮЛ / общая).
        owner_type = str(rec.get("ownershipType") or "").strip()
        if not owner_type and rights:
            owner_type = str((rec.get("rights") or [{}])[0].get("ownershipType") or "").strip()

        addr = rec.get("address") or {}
        return {
            "rights": rights,
            "encumbrances": encumbrances,
            "owner_type": owner_type,
            "owner_name": None,  # never expose ФЛ ФИО
            "registration_date": str(rec.get("regDate") or "").strip(),
            "cadastral_cost": str(rec.get("cadCost") or "").strip(),
            "land_category": str(rec.get("landCategory") or "").strip(),
            "permitted_use": str(rec.get("permittedUseByDoc") or rec.get("permittedUse") or "").strip(),
            "readable_address": str(addr.get("readableAddress") or "").strip() if isinstance(addr, dict) else "",
        }

    def _mock(self, cadastral_number: str, started: float) -> dict[str, Any]:
        return {
            "success": True,
            "source": "egrn_official:mock",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number,
            "balance": None,
            "rights": [{"type": "Собственность", "date": "01.03.2013", "number": "50-50-20/020/2013-320", "share": ""}],
            "encumbrances": [{"type": "Ипотека в силу закона", "number": "50-50-20/020/2013-322", "date": "01.03.2013", "is_risk": True}],
            "owner_type": "",
            "owner_name": None,
            "registration_date": "09.04.2014",
            "cadastral_cost": "5133262.85",
            "land_category": "",
            "permitted_use": "",
            "readable_address": "",
            "raw": {"mock": True, "cadastral_number": cadastral_number},
            "diagnostics": {"ok": True, "mode": "mock"},
            "warnings": ["egrn_official_mock_data_for_wiring_only"],
            "limitations": ["mock_data_not_a_real_egrn_extract"],
        }

    def _empty(self, cadastral_number: str, started: float, *, warning: str, success: bool = True) -> dict[str, Any]:
        return {
            "success": success,
            "source": f"egrn_official:{self.mode}",
            "elapsedMs": int((time.monotonic() - started) * 1000),
            "cadastralNumber": cadastral_number,
            "balance": None,
            "rights": [],
            "encumbrances": [],
            "owner_type": "",
            "owner_name": None,
            "registration_date": "",
            "cadastral_cost": "",
            "land_category": "",
            "permitted_use": "",
            "readable_address": "",
            "raw": {},
            "diagnostics": {"ok": success, "mode": self.mode, "warning": warning},
            "warnings": [warning],
            "limitations": [],
        }


class _EgrnTimeout(Exception):
    """Raised internally when an async newdb result does not arrive in time."""
