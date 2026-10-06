# PyInstaller spec — build with:  pyinstaller packaging/digitalworld.spec --noconfirm
# Produces dist/DigitalWorld/ with:
#   DigitalWorld.exe          the game (windowed)
#   DigitalWorldConsola.exe   same program with a console: --headless, tools, logs on screen
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "digitalworld", "content", "data"), os.path.join("digitalworld", "content", "data"))]
if os.path.isdir(os.path.join(ROOT, "assets")):
    datas.append((os.path.join(ROOT, "assets"), "assets"))

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["digitalworld.tools.inspect", "digitalworld.tools.screenshots"],
    excludes=["tkinter", "pandas", "matplotlib", "scipy", "pytest"],
)
pyz = PYZ(a.pure)
game = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DigitalWorld", console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DigitalWorldConsola", console=True)
coll = COLLECT(game, cli, a.binaries, a.datas, name="DigitalWorld")
