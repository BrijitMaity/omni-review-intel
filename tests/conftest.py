"""Root tests conftest: ensures omni-review-intel package is on sys.path."""
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
pkg_dir = root_dir / "omni-review-intel"
tests_dir = pkg_dir / "tests"

for p in (str(pkg_dir), str(tests_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)
