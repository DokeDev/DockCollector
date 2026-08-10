#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if sys.platform != "win32":
    sys.path.insert(0, str(ROOT / ".vendor"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("collector.app:app", host="127.0.0.1", port=3780, reload=False)
