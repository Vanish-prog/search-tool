#!/usr/bin/env python3
"""Isolated compositor smoke test; never connects to the live desktop.
Run inside the organizer Nix dev shell, passing the built GUI executable.
Keeps screenshot/logs in $TMPDIR and stops only processes it starts.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(tempfile.mkdtemp(prefix="fmqa-", dir=str(Path(os.environ["TMPDIR"]).parent)))
runtime = root / "runtime"
runtime.mkdir(mode=0o700)
config = root / "sway.conf"
config.write_text("seat seat0 fallback true\noutput HEADLESS-1 resolution 1200x850\n")
env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS="headless", WLR_RENDERER="gles2", WLR_RENDER_DRM_DEVICE="/dev/dri/renderD128", WLR_LIBINPUT_NO_DEVICES="1")
for key in ("DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK"):
    env.pop(key, None)
compositor = app = None
try:
    with (root / "sway.log").open("w") as log:
        compositor = subprocess.Popen(["sway", "--unsupported-gpu", "-c", str(config)], env=env, stdout=log, stderr=log)
    for _ in range(100):
        sockets = [p for p in runtime.glob("wayland-*") if not p.name.endswith(".lock")]
        if sockets:
            break
        if compositor.poll() is not None:
            raise RuntimeError("Isolated Sway failed; inspect sway.log")
        time.sleep(0.1)
    else:
        raise RuntimeError("No isolated Wayland socket")
    env["WAYLAND_DISPLAY"] = sockets[0].name
    with (root / "app.log").open("w") as log:
        app = subprocess.Popen([sys.argv[1], "--directory", "", "--query", "native smoke", "--reference", "/fixture/first.png", "--reference", "/fixture/second.txt"], env=env, stdout=log, stderr=log)
    time.sleep(3)
    if app.poll() is not None:
        raise RuntimeError("Native GUI exited early; inspect app.log")
    subprocess.run(["grim", str(root / "window.png")], env=env, check=True)
    print(f"Native window remained alive; screenshot/logs: {root}")
finally:
    for process in (app, compositor):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
