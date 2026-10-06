# PyInstaller spec — build with:  pyinstaller packaging/digitalworld.spec --noconfirm
# Produces dist/DigitalWorld/ (one-folder build: fast start, easy to inspect).
import os
from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "digitalworld", "content", "data"), os.path.join("digitalworld", "content", "data"))]
if os.path.isdir(os.path.join(ROOT, "assets")):
    datas.append((os.path.join(ROOT, "assets"), "assets"))

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["digitalworld.tools.inspect", "digitalworld.tools.screenshots"],
    excludes=["tkinter", "pandas", "matplotlib", "scipy"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DigitalWorld",
          console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DigitalWorld")
