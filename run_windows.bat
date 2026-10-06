@echo off
REM Run Digital World from source (first run installs dependencies).
python -m pip install -r requirements.txt --quiet
python -m digitalworld %*
