#!/usr/bin/env python3
"""
Runs monitor.py every hour in an infinite loop.
Used for Railway deployment.
"""
import time
import subprocess
import sys
from datetime import datetime, timezone

print("=== Monitor Loop Started ===", flush=True)

while True:
    now = datetime.now(timezone.utc)
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} UTC] Running monitor...", flush=True)
    
    try:
        result = subprocess.run(
            [sys.executable, "monitor.py"],
            capture_output=False,
            timeout=120
        )
        print(f"Monitor exited with code {result.returncode}", flush=True)
    except Exception as e:
        print(f"Error running monitor: {e}", flush=True)
    
    print("Sleeping 60 minutes...", flush=True)
    time.sleep(3600)  # 1 година
