@echo off
REM Build a standalone Windows folder build in dist\DigitalWorld\
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
pyinstaller packaging\digitalworld.spec --noconfirm || goto :error
echo.
echo Done: dist\DigitalWorld\DigitalWorld.exe
goto :eof
:error
echo Build failed.
exit /b 1
