import os
import sys
import tempfile
from pathlib import Path

os.environ["KARAOKE_DATA_DIR"] = tempfile.mkdtemp()
os.environ["KARAOKE_API_KEY"] = "secret"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
