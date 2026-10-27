"""The dataserver's modules are flat siblings of this folder's parent (it runs
as `python dataserver.py`, not as a package), so the tests import them by bare
name with that directory on the path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
