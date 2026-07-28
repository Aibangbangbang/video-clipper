#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH=$(pwd)
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
