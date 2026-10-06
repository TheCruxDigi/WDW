"""Frozen-app entry point (PyInstaller)."""
import sys

from digitalworld.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
