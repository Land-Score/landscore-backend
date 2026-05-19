import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'data-collector'))

from app.rosreestr_client import RealRosreestrClient
from app.sources.nspd_map_layers import NspdChildObjectClient

async def main(cad: str):
    rc = RealRosreestrClient()
    plot = await rc.get_plot(cad)
    print('plot success', bool(plot))
    if not plot:
        return
    client = NspdChildObjectClient()
    child, parts, composition, warnings = await client.collect_for_plot(cadastral_number=cad, plot_raw_json=plot.raw_json or {})
    print('child_objects', len(child))
    print('land_parts', len(parts))
    print('composition', len(composition))
    print('warnings', warnings)
    print(json.dumps(composition[:5], ensure_ascii=False, indent=2)[:4000])

if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else '26:11:101101:53'))
