#!/usr/bin/env python3
"""
One-time setup for the review app.

  python install.py            install backend (pip) + frontend (npm) deps
  python install.py --backend  backend Python deps only
  python install.py --frontend frontend npm deps only

Use the Python env you intend to run the app with (the one that has pandas,
e.g. your mambaforge) so the backend deps land in the right place.
Then start it with:  python run.py
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "backend")
FRONTEND = os.path.join(HERE, "frontend")
IS_WIN = os.name == "nt"
NPM = "npm.cmd" if IS_WIN else "npm"


def run(cmd, cwd, **kw):
    print(f">>> {' '.join(cmd)}  (in {os.path.relpath(cwd, HERE) or '.'})")
    subprocess.run(cmd, cwd=cwd, check=True, **kw)


def backend_command():
    return [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]


def frontend_command():
    return [NPM, "install"]


def selection(args):
    """Which sides to install. Default (neither flag) = both."""
    do_backend = args.backend or not args.frontend
    do_frontend = args.frontend or not args.backend
    return do_backend, do_frontend


def install_backend():
    print(f"\n== Backend deps (Python {sys.version.split()[0]} at {sys.executable}) ==")
    run(backend_command(), cwd=BACKEND)


def install_frontend():
    print("\n== Frontend deps (npm) ==")
    if shutil.which(NPM) is None and shutil.which("npm") is None:
        sys.exit("npm not found on PATH. Install Node.js, then re-run.")
    run(frontend_command(), cwd=FRONTEND, shell=IS_WIN)


def main():
    ap = argparse.ArgumentParser(description="Install review app dependencies.")
    ap.add_argument("--backend", action="store_true", help="backend deps only")
    ap.add_argument("--frontend", action="store_true", help="frontend deps only")
    args = ap.parse_args()

    do_backend, do_frontend = selection(args)
    if do_backend:
        install_backend()
    if do_frontend:
        install_frontend()

    print("\nDone. Start the app with:  python run.py")


if __name__ == "__main__":
    main()
