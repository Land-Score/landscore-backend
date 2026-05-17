#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-collector"))

from app.dataset_pipeline import DataCollectionPipeline


async def _run(cadastral_number: str, output: Path | None) -> None:
    dataset = await DataCollectionPipeline().collect_full_dataset(cadastral_number)
    text = json.dumps(dataset, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"saved={output}")
    print(f"success={dataset.get('success')}")
    print(f"cadastral_number={dataset.get('cadastralNumber')}")
    print(f"source={dataset.get('source')}")
    if dataset.get("warnings"):
        print("warnings=" + "; ".join(dataset["warnings"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect full LandScore dataset for one cadastral number.")
    parser.add_argument("--cadastral-number", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.cadastral_number, args.output))


if __name__ == "__main__":
    main()

