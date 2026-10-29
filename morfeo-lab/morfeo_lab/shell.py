"""Running external programs (docker, kind, kubectl, kubectl exec...). Everything shell-related lives here."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path

from .console import dim, emit
from .errors import LabError
from .paths import ROOT
from .units import fmt_duration

VERBOSE = bool(os.environ.get("MORFEO_VERBOSE"))

INSTALL_HINTS = {
    "docker": "install Docker Desktop (https://docs.docker.com/desktop/) and start it",
    "kind": "Windows: winget install Kubernetes.kind · macOS: brew install kind · Linux: https://kind.sigs.k8s.io",
    "kubectl": "Windows: winget install Kubernetes.kubectl · macOS: brew install kubectl · "
               "Linux: https://kubernetes.io/docs/tasks/tools/",
}


def cmd_name() -> str:
    """How the user runs the lab, for hints that can be pasted: .\\morfeo-lab.cmd, ./morfeo-lab, morfeo-lab."""
    if os.environ.get("MORFEO_LAUNCHER") == "wrapper":
        here = Path.cwd().resolve() == ROOT
        if os.name == "nt":
            return r".\morfeo-lab.cmd" if here else f'"{ROOT / "morfeo-lab.cmd"}"'
        return "./morfeo-lab" if here else str(ROOT / "morfeo-lab")
    return "morfeo-lab"


def which(tool: str) -> str | None:
    return shutil.which(tool)


def require_tools(*tools: str):
    missing = [t for t in tools if which(t) is None]
    if missing:
        hints = "\n".join(f"{t}: {INSTALL_HINTS.get(t, 'install it and add it to the PATH')}" for t in missing)
        raise LabError(f"Not installed on this machine: {', '.join(missing)}\n{hints}")


def _show(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def run(cmd: list[str], *, check=True, capture=False, input: str | None = None,
        quiet=False, timeout: float | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command and return it. `capture` keeps stdout and stderr; `quiet` hides them."""
    if VERBOSE and not quiet:
        emit(dim("    $ " + _show(cmd)))
    try:
        res = subprocess.run(
            cmd, input=input, text=True, timeout=timeout,
            stdout=subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None),
            stderr=subprocess.PIPE if (capture or quiet) else None,
            encoding="utf-8", errors="replace", env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as e:
        raise LabError(f"Program not found: '{cmd[0]}'. {INSTALL_HINTS.get(cmd[0], '')}") from e
    except subprocess.TimeoutExpired as e:
        if not check:   # the caller looks at the exit code itself: 124 is the one `timeout` uses
            return subprocess.CompletedProcess(cmd, 124, "", "timed out")
        raise LabError(f"Command timed out after {fmt_duration(timeout or 0)}: {_show(cmd)}") from e
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip() if (capture or quiet) else ""
        raise LabError(f"Command failed (exit code {res.returncode}): {_show(cmd)}\n{detail}")
    return res


def run_streaming(cmd: list[str], *, on_line=None, timeout: float | None = None,
                  keep: int = 40) -> subprocess.CompletedProcess:
    """Run a long command reading its output line by line (stdout and stderr together): `on_line(line)`
    can update a spinner. The last `keep` lines are returned in `.stdout` for error messages."""
    if VERBOSE:
        emit(dim("    $ " + _show(cmd)))
    tail: deque[str] = deque(maxlen=keep)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except FileNotFoundError as e:
        raise LabError(f"Program not found: '{cmd[0]}'. {INSTALL_HINTS.get(cmd[0], '')}") from e
    timed_out = threading.Event()

    def expire():   # a silent command (a long pull or build step) must not outlive its time limit
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, expire) if timeout else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                if on_line:
                    on_line(line)
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    finally:
        if timer:
            timer.cancel()
        proc.stdout.close()
    if timed_out.is_set():
        raise LabError(f"Command timed out after {fmt_duration(timeout)}: {_show(cmd)}")
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout="\n".join(tail), stderr="")


def run_bytes(cmd: list[str], data: bytes | None = None, timeout: float | None = 600, check=True) -> bytes:
    """Binary-safe variant (tar streams in and out of pods). No shell and no text mode, so it behaves the
    same on Windows."""
    try:
        res = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        if not check:
            return b""
        raise LabError(f"Command timed out after {fmt_duration(timeout or 0)}: {_show(cmd)}") from e
    except FileNotFoundError as e:
        raise LabError(f"Program not found: '{cmd[0]}'. {INSTALL_HINTS.get(cmd[0], '')}") from e
    if check and res.returncode != 0:
        raise LabError(f"Command failed (exit code {res.returncode}): {_show(cmd)}\n"
                       + res.stderr.decode("utf-8", "replace").strip())
    return res.stdout


def run_to_files(cmd: list[str], *, stdin=None, stdout=None, timeout: float = 1800):
    """A binary command whose input or output is a file handle: big payloads never sit in memory."""
    try:
        res = subprocess.run(cmd, stdin=stdin, stdout=stdout or subprocess.DEVNULL, stderr=subprocess.PIPE,
                             timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise LabError(f"Command timed out after {fmt_duration(timeout)}: {_show(cmd)}") from e
    if res.returncode != 0:
        raise LabError(f"Command failed (exit code {res.returncode}): {_show(cmd)}\n"
                       + res.stderr.decode("utf-8", "replace").strip()[-500:])


def out(cmd: list[str], check=True, timeout: float | None = 60) -> str:
    """The stripped standard output of a command."""
    return (run(cmd, check=check, capture=True, quiet=True, timeout=timeout).stdout or "").strip()
