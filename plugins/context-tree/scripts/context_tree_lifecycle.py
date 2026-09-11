#!/usr/bin/env python3
"""Keep the Context Tree float aligned with the Codex Desktop lifecycle."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import context_tree as ct
from context_tree_float import codex_desktop_running


ROOT = Path(__file__).resolve().parent.parent
STORE = ct.store_dir()
_MUTEX_HANDLE: Any = None
WINDOWS_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    suffix = hashlib.sha256(str(STORE).lower().encode("utf-8")).hexdigest()[:12]
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, f"Local\\ContextTreeLifecycle_{suffix}")
    return bool(_MUTEX_HANDLE) and kernel32.GetLastError() != 183


def launch_float() -> subprocess.Popen[Any]:
    command = [sys.executable, str(ROOT / "scripts" / "context_tree_float.py"), "--follow-codex"]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = WINDOWS_CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


def run() -> None:
    child: subprocess.Popen[Any] | None = None
    child_started_at = 0.0
    last_launch_at = 0.0
    failed_launches = 0
    codex_was_running = False
    while True:
        enabled = bool(ct.load_config(STORE).get("float_enabled", True))
        codex_running = codex_desktop_running()
        if not codex_running:
            # Reset the launch budget only after the official desktop app has
            # actually gone away, so a crashing child cannot restart forever.
            if child is not None and child.poll() is None:
                child.terminate()
            child = None
            failed_launches = 0
            last_launch_at = 0.0
        elif not enabled:
            if child is not None and child.poll() is None:
                child.terminate()
            child = None
        else:
            if codex_running and not codex_was_running:
                failed_launches = 0
                last_launch_at = 0.0
            if child is not None and child.poll() is not None:
                if time.monotonic() - child_started_at < 3:
                    failed_launches += 1
                child = None
            if (
                child is None and failed_launches < 3
                and time.monotonic() - last_launch_at >= 10
            ):
                child = launch_float()
                child_started_at = time.monotonic()
                last_launch_at = child_started_at
        codex_was_running = codex_running
        time.sleep(2)


if __name__ == "__main__":
    if acquire_single_instance():
        run()
