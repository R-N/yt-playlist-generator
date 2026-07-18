#!/usr/bin/env python3
"""
Run the review app.

Modes:
  python run.py            built frontend (default): npm build, then uvicorn
                           serves the SPA + API together on one port.
  python run.py --dev      dev frontend: uvicorn (reload) + `npm run dev`
                           (Vite on :5173, proxies /api to the backend).

Options:
  --port N      backend port (default 8000)
  --host H      backend host (default localhost)
  --no-build    built mode only: skip the rebuild, serve existing dist/
  --no-install  skip the automatic `npm install`

In built mode the SPA is at  http://HOST:PORT
In dev mode open Vite at      http://localhost:5173
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "backend")
FRONTEND = os.path.join(HERE, "frontend")
DIST = os.path.join(FRONTEND, "dist")
IS_WIN = os.name == "nt"
NPM = "npm.cmd" if IS_WIN else "npm"


def run(cmd, cwd, **kw):
    """Run a command, raising on failure. cmd is a list; npm uses shell on Win."""
    print(f">>> {' '.join(cmd)}  (in {os.path.relpath(cwd, HERE) or '.'})")
    return subprocess.run(cmd, cwd=cwd, check=True, **kw)


def ensure_npm_deps(skip_install):
    if skip_install:
        return
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        print("node_modules missing -> npm install")
        run([NPM, "install"], cwd=FRONTEND, shell=IS_WIN)


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def uvicorn_cmd(args, reload=False):
    cmd = [sys.executable, "-m", "uvicorn", "main:app",
           "--host", args.host, "--port", str(args.port)]
    if reload:
        cmd.append("--reload")
    return cmd


def built_commands(args):
    """Ordered (cmd, cwd) steps for built mode. Pure -> unit-testable."""
    steps = []
    if not args.no_build:
        steps.append(([NPM, "run", "build"], FRONTEND))
    steps.append((uvicorn_cmd(args), BACKEND))
    return steps


def dev_commands(args):
    """The two long-running (cmd, cwd) processes for dev mode."""
    return [
        (uvicorn_cmd(args, reload=True), BACKEND),
        ([NPM, "run", "dev"], FRONTEND),
    ]


def serve_built(args):
    ensure_npm_deps(args.no_install)
    steps = built_commands(args)
    for cmd, cwd in steps[:-1]:               # build step(s)
        run(cmd, cwd=cwd, shell=IS_WIN)
    if not os.path.isdir(DIST):
        sys.exit("No frontend/dist/. Build first (drop --no-build).")
    # main.py mounts dist/ at import time, so build must already be done.
    print(f"\nServing app + API at http://{args.host}:{args.port}\n")
    cmd, cwd = steps[-1]                       # uvicorn (foreground)
    run(cmd, cwd=cwd)


def serve_dev(args):
    ensure_npm_deps(args.no_install)
    (be_cmd, be_cwd), (fe_cmd, fe_cwd) = dev_commands(args)
    backend = subprocess.Popen(be_cmd, cwd=be_cwd)
    frontend = subprocess.Popen(fe_cmd, cwd=fe_cwd, shell=IS_WIN)
    print(f"\nBackend  http://{args.host}:{args.port}")
    print("Frontend http://localhost:5173  (open this)\n")
    procs = [backend, frontend]
    try:
        # exit as soon as either process dies
        while all(p.poll() is None for p in procs):
            for p in procs:
                try:
                    p.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        for p in procs:
            kill_tree(p)


def build_parser():
    ap = argparse.ArgumentParser(description="Run the review app.")
    ap.add_argument("--dev", action="store_true",
                    help="dev frontend (Vite) instead of built")
    ap.add_argument("--port", type=int, default=8000)
    # localhost, not 127.0.0.1: YouTube's iframe player rejects embeds whose
    # referer origin is a bare IP ("Video unavailable"); a hostname passes.
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--no-build", action="store_true",
                    help="built mode: skip rebuild, serve existing dist/")
    ap.add_argument("--no-install", action="store_true",
                    help="skip automatic npm install")
    return ap


def main():
    args = build_parser().parse_args()
    (serve_dev if args.dev else serve_built)(args)


if __name__ == "__main__":
    main()
