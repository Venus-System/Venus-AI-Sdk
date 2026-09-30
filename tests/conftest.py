import sys
from pathlib import Path

# Permite `from _fakes import ...` em qualquer subpasta de tests/.
sys.path.insert(0, str(Path(__file__).parent))
