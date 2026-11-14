"""Регресія 14.11: у monitor_loop.py не має бути невизначених імен (now_local ковтався except-ом)."""
import subprocess, sys
r = subprocess.run([sys.executable, "-m", "pyflakes", "monitor_loop.py"], capture_output=True, text=True)
bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
print("\n".join(bad) or "ok: невизначених імен нема")
sys.exit(1 if bad else 0)
