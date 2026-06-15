from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_harbor_site_packages() -> None:
    """Make Harbor's uv-tool package importable in local TB2 runs."""

    candidates = []
    if os.environ.get("HARBOR_PYTHONPATH"):
        candidates.append(Path(os.environ["HARBOR_PYTHONPATH"]))
    candidates.append(
        Path("/home/xiewei/.local/share/uv/tools/harbor/lib/python3.13/site-packages")
    )

    for candidate in candidates:
        if (candidate / "harbor").is_dir():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
