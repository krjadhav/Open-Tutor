#!/usr/bin/env bash
exec python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
