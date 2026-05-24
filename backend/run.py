"""Bootstrap CallScope (Phase 0). Run: python backend/run.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
