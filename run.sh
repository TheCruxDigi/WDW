#!/usr/bin/env sh
# Run Digital World from source.
python3 -m pip install -r requirements.txt --quiet
exec python3 -m digitalworld "$@"
