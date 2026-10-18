import sys
from pathlib import Path

# Make the project root importable no matter where pytest is started from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
