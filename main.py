"""
Root launcher for CLI.
Allows running `python main.py --url ...` directly from the root workspace directory.
"""
import sys
import os
from pathlib import Path

package_dir = Path(__file__).resolve().parent / "omni-review-intel"
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))

os.chdir(str(package_dir))

from main import main

if __name__ == "__main__":
    main()
    
