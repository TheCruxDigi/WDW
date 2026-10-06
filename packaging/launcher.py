"""Frozen-app entry point (PyInstaller).

``DigitalWorldConsola.exe inspect <command> ...`` runs the diagnostics CLI;
any other arguments go to the game (e.g. ``--headless --ticks 1440``).
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "inspect":
        from digitalworld.tools.inspect import main as inspect_main
        sys.exit(inspect_main(sys.argv[2:]))
    from digitalworld.__main__ import main
    sys.exit(main())
