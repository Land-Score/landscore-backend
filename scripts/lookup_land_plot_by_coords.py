from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_COLLECTOR_ROOT = BACKEND_ROOT / "services" / "data-collector"
sys.path.insert(0, str(DATA_COLLECTOR_ROOT))

from app.rosreestr_client import egrn_to_dict, get_client, plot_to_dict  # noqa: E402


def _json_default(value: Any) -> str:
    return str(value)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.mode)
    if args.no_verify_ssl and hasattr(client, "verify_ssl"):
        client.verify_ssl = False
    plot = await client.get_plot_by_coordinates(args.lat, args.lng, args.radius_m)
    egrn = await client.get_egrn(plot.cadastral_number) if args.include_egrn_note else None

    result: dict[str, Any] = {
        "query": {
            "lat": args.lat,
            "lng": args.lng,
            "radius_m": args.radius_m,
            "mode": args.mode,
        },
        "plot": plot_to_dict(plot),
        "source": {
            "name": "NSPD public cadastral data" if args.mode == "real" else "mock",
            "legal_note": (
                "Это справочные публичные кадастровые данные. Полная юридически значимая "
                "выписка ЕГРН, сведения о правообладателях и обременениях требуют официального запроса."
            ),
        },
    }
    if egrn:
        result["egrn"] = egrn_to_dict(egrn)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lookup public cadastral land plot data by WGS84 coordinates via NSPD/Rosreestr."
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude in WGS84, e.g. 55.7558")
    parser.add_argument("--lng", type=float, required=True, help="Longitude in WGS84, e.g. 37.6176")
    parser.add_argument("--radius-m", type=float, default=2.0, help="Search buffer radius in meters")
    parser.add_argument("--mode", choices=("mock", "real"), default=os.getenv("ROSREESTR_MODE", "mock"))
    parser.add_argument("--include-egrn-note", action="store_true", help="Include official EGRN limitation note")
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable TLS verification only if the local machine lacks Russian root certificates",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")
    args = parser.parse_args()

    result = asyncio.run(_run(args))
    json_text = json.dumps(
        result,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        default=_json_default,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_text + "\n", encoding="utf-8")
    print(json_text)


if __name__ == "__main__":
    main()
