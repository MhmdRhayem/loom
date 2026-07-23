# Root conftest: put the repository root on sys.path so tests can import both the
# `backend` core package and the `demo` package. Loom runs as a flat app (no editable
# install), so imports resolve from the repo root rather than from site-packages.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
