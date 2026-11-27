import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parent.parent / "omni-review-intel"
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))
_tests = _pkg / "tests"
if str(_tests) not in sys.path:
    sys.path.insert(0, str(_tests))

from test_s1_input_analyzer import *  # noqa: F401, F403
