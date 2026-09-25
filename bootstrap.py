# Python isolated mode deliberately omits the script directory; add only our bundled code.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_backend import main
main()
