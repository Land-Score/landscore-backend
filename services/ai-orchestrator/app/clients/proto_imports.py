from __future__ import annotations

import sys
from pathlib import Path


def add_generated_proto_path() -> None:
    generated_dir = Path(__file__).resolve().parents[1] / "proto_gen"
    if generated_dir.exists() and str(generated_dir) not in sys.path:
        sys.path.insert(0, str(generated_dir))
