"""
Root launcher for Streamlit UI.
Allows running `streamlit run app.py` directly from the root workspace directory.
"""
import sys
from pathlib import Path

# Add omni-review-intel to sys.path
package_dir = Path(__file__).resolve().parent / "omni-review-intel"
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))

import os
os.chdir(str(package_dir))

app_path = package_dir / "app.py"
with open(app_path, "r", encoding="utf-8") as f:
    code = compile(f.read(), str(app_path), "exec")
    exec(code, globals())
