import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-collector"))

from app.config import settings
from app.rosreestr_client import _extract_features
from app.sources.nspd_map_layers import _feature_category_id_for_tabs, _feature_geom_id, _feature_registers_id


HEADERS = {
    "Accept": "application/json,*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Referer": "https://nspd.gov.ru/map?thematic=PKK&baseLayerId=235&theme_id=1",
    "User-Agent": settings.rosreestr_user_agent,
    "X-Public-User": "true",
}


async def get_json(client: httpx.AsyncClient, path: str):
    try:
        response = await client.get(path)
        print("GET", path, response.status_code)
        if response.status_code >= 400:
            print(response.text[:300])
            return None
        return response.json()
    except Exception as exc:
        print("ERR", path, repr(exc))
        return None


async def main(cad: str):
    query = quote(cad.strip(), safe="")
    async with httpx.AsyncClient(
        base_url=settings.rosreestr_api_url.rstrip("/"),
        headers=HEADERS,
        timeout=15,
        verify=settings.rosreestr_verify_ssl and not settings.nspd_insecure_tls,
        follow_redirects=True,
    ) as client:
        search_paths = (
            f"/geoportal/v2/search/geoportal?thematicSearchId=1&query={query}&CRS=EPSG:3857",
            f"/geoportal/v2/search/geoportal?query={query}&CRS=EPSG:3857",
            f"/geoportal/v1/search/geoportal?query={query}&CRS=EPSG:3857",
        )
        feature = None
        for path in search_paths:
            payload = await get_json(client, path)
            features = _extract_features(payload or {})
            if features:
                feature = features[0]
                break
        if not feature:
            print("NO_FEATURE")
            return

        category_id = _feature_category_id_for_tabs(feature)
        geom_id = _feature_geom_id(feature)
        registers_id = _feature_registers_id(feature)
        print("ids", {"category_id": category_id, "geom_id": geom_id, "registers_id": registers_id})

        tab_classes = (
            "compositionLand",
            "landComposition",
            "ezpComposition",
            "landUse",
            "landParts",
            "objectsList",
        )
        paths = []
        for tab in tab_classes:
            paths.extend(
                [
                    f"/geoportal/v1/tab-values-data?tabClass={tab}&categoryId={category_id}&geomId={geom_id}",
                    f"/geoportal/v1/tab-group-data?tabClass={tab}&categoryId={category_id}&geomId={geom_id}",
                ]
            )
            if registers_id:
                paths.append(f"/geoportal/v1/tab-values-data?tabClass={tab}&objdocId={geom_id}&registersId={registers_id}")

        for path in paths:
            payload = await get_json(client, path)
            if payload not in (None, {}, []):
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                print("PAYLOAD", path)
                print(text[:5000])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "26:11:101101:53"))
