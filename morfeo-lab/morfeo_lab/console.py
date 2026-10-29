"""Terminal output: colours, aligned tables, spinners for long steps and the live status line.

It works in PowerShell, the classic Windows console and terminals without colour (NO_COLOR, TERM=dumb,
redirected output)."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
import threading
import time

from .units import fmt_duration

if os.name == "nt":   # enables ANSI escape codes in Windows 10+ consoles
    os.system("")
for _stream in (sys.stdout, sys.stderr):   # never fail on a console that cannot print ✔ ✖ …
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(errors="replace")

TTY = sys.stdout.isatty()
_COLOR = TTY and os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"
# The classic Windows console (conhost with Consolas) has no glyph for ✔ ✖ or braille spinners;
# Windows Terminal and VS Code do. The ASCII-safe symbols below exist everywhere.
_CLASSIC_CONSOLE = os.name == "nt" and not (os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))
SYM = {
    "ok": "√" if _CLASSIC_CONSOLE else "✔", "err": "×" if _CLASSIC_CONSOLE else "✖", "warn": "!", "dot": "•",
    "spin": "|/-\\" if _CLASSIC_CONSOLE else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "bar": "█", "bar_bg": "░", "sep": "│",
}
_ANSI = re.compile(r"\033\[[0-9;]*m")
_lock = threading.RLock()


def color(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


def bold(s: str) -> str:
    return color("1", s)


def dim(s: str) -> str:
    return color("2", s)


def visible_len(s: str) -> int:
    return len(_ANSI.sub("", s))


def term_width() -> int:
    return max(40, shutil.get_terminal_size((100, 24)).columns)


def _fit(s: str, width: int) -> str:
    """Cut a (possibly coloured) line to `width` visible characters: a wrapped live line can no longer be
    rewritten in place."""
    if visible_len(s) <= width:
        return s
    out, n, i = [], 0, 0
    while i < len(s) and n < width - 1:
        m = _ANSI.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(s[i])
        n += 1
        i += 1
    return "".join(out) + "…" + ("\033[0m" if _COLOR else "")


class _LiveLine:
    """The single status line at the bottom of the terminal (progress of a test, spinner of a long step).
    Normal messages are printed above it. Without a terminal (CI, redirected output) it prints a plain line
    at most every `every` seconds instead."""

    def __init__(self):
        self.text = ""
        self.shown = False
        self.last_plain = 0.0

    def _clear(self):
        if self.shown:
            sys.stdout.write("\r\033[K")
            self.shown = False

    def _draw(self):
        if self.text and TTY:
            sys.stdout.write("\r\033[K" + _fit(self.text, term_width() - 1))
            sys.stdout.flush()
            self.shown = True

    def update(self, text: str, every: float = 15.0):
        with _lock:
            self.text = text
            if TTY:
                self._draw()
            elif time.time() - self.last_plain >= every:
                print(_ANSI.sub("", text), flush=True)
                self.last_plain = time.time()

    def stop(self):
        with _lock:
            self._clear()
            sys.stdout.flush()
            self.text = ""
            self.last_plain = 0.0


LIVE = _LiveLine()


def emit(line: str, stream=None):
    with _lock:
        LIVE._clear()
        print(line, file=stream or sys.stdout, flush=True)
        LIVE._draw()


def step(msg: str):
    emit("\n" + color("1;36", "==> ") + bold(msg))


def info(msg: str):
    emit("    " + msg)


def ok(msg: str):
    emit(color("32", f"  {SYM['ok']} ") + msg)


def _more(lines: list[str]) -> str:
    """Continuation lines under a message, aligned with its text."""
    return "".join("\n" + (x if x.startswith("    ") else "    " + x.strip()) for x in lines)


def warn(msg: str):
    first, *rest = str(msg).splitlines() or [""]
    emit(color("33", f"  {SYM['warn']} ") + first + _more(rest))


def error(msg: str):
    first, *rest = str(msg).splitlines() or [""]
    emit(color("1;31", f"  {SYM['err']} ") + bold(first) + _more(rest), stream=sys.stderr if not TTY else None)


def hint(msg: str):
    """A suggested next command."""
    emit("    " + dim("→ ") + msg)


def kv(key: str, value: str, width: int = 13):
    """An aligned 'key   value' line under a step."""
    emit("    " + dim(f"{key:<{width}}") + value)


_NUMERIC = re.compile(r"^[-+≤≥<>≈]?\s?[\d.,]+\s?(%|ms|s|req/s|cores?|MiB|GiB|B)?$")


def table(headers: list[str], rows: list[list[str]], indent: int = 4, align: str | None = None):
    """A plain aligned table: dim header, numeric columns right-aligned. `align` forces one letter per
    column ('l' or 'r')."""
    if not rows:
        return
    cols = len(headers)
    width = [max(visible_len(str(r[i])) for r in [headers, *rows]) for i in range(cols)]
    if align is None:
        def numeric(i):
            vals = [_ANSI.sub("", str(r[i])).strip() for r in rows]
            return any(v not in ("–", "") for v in vals) and all(_NUMERIC.match(v) or v in ("–", "") for v in vals)
        align = "".join("r" if numeric(i) else "l" for i in range(cols))

    def cell(i, v):
        v = str(v)
        pad = " " * (width[i] - visible_len(v))
        return pad + v if align[i] == "r" else v + pad
    gap = "  "
    emit(" " * indent + dim(gap.join(cell(i, h) for i, h in enumerate(headers)).rstrip()))
    for r in rows:
        emit(" " * indent + gap.join(cell(i, v) for i, v in enumerate(r)).rstrip())


class Task:
    def __init__(self, msg: str):
        self.msg, self.detail, self.t0 = msg, "", time.time()


@contextlib.contextmanager
def task(msg: str, done: str | None = None):
    """A long step with a spinner and the elapsed time. `t.detail = '...'` shows what is happening now
    (for example the current docker build step). Prints '✔ <done or msg> (time)' at the end, or nothing
    if an exception escapes."""
    state = Task(msg)
    stop = threading.Event()

    def spin():
        frames, i = SYM["spin"], 0
        while TTY and not stop.wait(0.12):   # without a terminal only the start and end lines are printed
            el = fmt_duration(time.time() - state.t0)
            d = f" · {state.detail}" if state.detail else ""
            LIVE.update(f"  {color('36', frames[i % len(frames)])} {msg}{dim(d)} {dim(el)}", every=60)
            i += 1

    th = threading.Thread(target=spin, daemon=True)
    if not TTY:
        emit(f"    {msg}…")
    th.start()
    try:
        yield state
    finally:
        stop.set()
        th.join()
        LIVE.stop()
    el = time.time() - state.t0
    ok((done or msg) + (dim(f" ({fmt_duration(el)})") if el >= 2 else ""))


def progress_bar(fraction: float, width: int = 18) -> str:
    f = min(1.0, max(0.0, fraction))
    n = round(f * width)
    return color("36", SYM["bar"] * n) + dim(SYM["bar_bg"] * (width - n))
