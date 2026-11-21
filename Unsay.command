#!/bin/bash
cd "$(dirname "$0")"
PY=$(command -v python3.10 || command -v python3)
"$PY" app.py
