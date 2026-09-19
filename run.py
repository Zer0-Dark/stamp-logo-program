"""Entry point. `python run.py`, or the frozen LogoStamper.exe."""
import multiprocessing
import sys

from logostamper.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
