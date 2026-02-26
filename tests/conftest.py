import sys
from pathlib import Path

# Make src/ importable by all tests without installing the project as a package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
