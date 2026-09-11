#!/usr/bin/env python3
"""Compact always-on-top Context Tree activity monitor for Codex Desktop."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any

import context_tree as ct


ROOT = Path(__file__).resolve().parent.parent
STORE = ct.store_dir()

BG = "#f6f7f8"
SURFACE = "#ffffff"
INK = "#17191c"
MUTED = "#70757d"
LINE = "#e6e8eb"
TRACK = "#e9ecef"
GREEN = "#169c76"
AMBER = "#d48a18"
RED = "#d95555"
IDLE = "#69717c"
FONT = "Microsoft YaHei UI"
MONO = "Cascadia Mono"
_MUTEX_HANDLE: Any = None
COLLAPSED_REFRESH_MS = 15_000
EXPANDED_REFRESH_MS = 5_000


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    suffix = hashlib.sha256(str(STORE).lower().encode("utf-8")).hexdigest()[:12]
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, f"Local\\ContextTreeFloat_{suffix}")
    return bool(_MUTEX_HANDLE) and kernel32.GetLastError() != 183


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_number(value: Any) -> str:
    amount = number(value)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return str(int(amount))


def format_seconds(milliseconds: Any) -> str:
    if milliseconds is None:
        return "--"
    seconds = number(milliseconds) / 1000
    precision = 2 if seconds < 10 else 1 if seconds < 100 else 0
    return f"{seconds:.{precision}f} 秒"


def should_show_float(payload: dict[str, Any]) -> bool:
    sessions = payload.get("active_sessions", [])
    persistent = bool(payload.get("config", {}).get("float_persistent", False))
    return bool(sessions or persistent)


def _official_codex_path(path: str) -> bool:
    normalized = str(path or "").replace("/", "\\").lower()
    return bool(re.search(r"\\openai\.codex_[^\\]+\\app\\resources\\codex\.exe$", normalized))


def codex_desktop_running() -> bool:
    """Return whether the installed official Codex Desktop process is running."""
    if os.name == "nt":
        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260),
            ]
        try:
            kernel32 = ctypes.windll.kernel32
            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                return False
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            found = False
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == "codex.exe":
                        handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                        if handle:
                            buffer = ctypes.create_unicode_buffer(1024)
                            size = ctypes.c_ulong(len(buffer))
                            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                                found = _official_codex_path(buffer.value)
                            kernel32.CloseHandle(handle)
                        if found:
                            break
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
            kernel32.CloseHandle(snapshot)
            return found
        except (AttributeError, OSError):
            return False
        return "codex.exe" in completed.stdout.lower()
    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "comm="], capture_output=True, text=True,
            timeout=4, check=False,
        )
    except OSError:
        return False
    return any("codex" in line.lower() for line in completed.stdout.splitlines())


class ContextTreeFloat:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Context Tree")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(bg=LINE)
        self.root.resizable(False, False)
        self.root.withdraw()
        self.expanded = "--expanded" in sys.argv
        self.visible = False
        self.payload: dict[str, Any] = {}
        self._refresh_in_flight = False
        self._refresh_results: queue.SimpleQueue[tuple[dict[str, Any] | None, Exception | None]] = queue.SimpleQueue()
        self.drag_origin = (0, 0)
        self.session_signature = ""
        self.expanded_epochs: set[str] = set()
        self.follow_codex = "--follow-codex" in sys.argv
        self.codex_missing_since: float | None = None
        self._build_shell()
        self.refresh()
        self.root.after(100, self._poll_refresh)
        self.root.after(EXPANDED_REFRESH_MS if self.expanded else COLLAPSED_REFRESH_MS, self._tick)

    def _build_shell(self) -> None:
        self.shell = tk.Frame(self.root, bg=SURFACE, bd=0)
        self.shell.pack(fill="both", expand=True, padx=1, pady=1)
        self.collapsed = tk.Button(
            self.shell,
            text="CT",
            command=self.expand,
            bg=GREEN,
            fg="white",
            activebackground=GREEN,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(MONO, 11, "bold"),
        )
        self.collapsed.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.collapsed.bind("<B1-Motion>", self._drag_move, add="+")
        self.panel = tk.Frame(self.shell, bg=SURFACE)
        if self.expanded:
            self._show_panel()
        else:
            self._show_button()

    def _show_button(self) -> None:
        was_expanded = self.expanded
        old_x, old_y = self.root.winfo_x(), self.root.winfo_y()
        old_width = max(52, self.root.winfo_width())
        self.expanded = False
        self.panel.pack_forget()
        self.collapsed.pack(fill="both", expand=True)
        if was_expanded:
            bounds = self._monitor_work_area(old_x + old_width - 1, old_y + 26)
            x, y = self._clamp_position(old_x + old_width - 52, old_y, 52, 52, bounds)
        else:
            bounds = self._monitor_work_area(self.root.winfo_x(), self.root.winfo_y())
            x, y = bounds[2] - 76, max(bounds[1] + 24, bounds[1] + 150)
        self._set_geometry(52, 52, x, y)

    def _show_panel(self) -> None:
        old_x, old_y = self.root.winfo_x(), self.root.winfo_y()
        old_width = max(52, self.root.winfo_width())
        self.expanded = True
        self.collapsed.pack_forget()
        self.panel.pack(fill="both", expand=True)
        bounds = self._monitor_work_area(old_x + old_width // 2, old_y + 26)
        x, y = self._clamp_position(old_x + old_width - 430, old_y, 430, 420, bounds)
        self._set_geometry(430, 420, x, y)

    def _set_geometry(self, width: int, height: int, x: int, y: int) -> None:
        self.root.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _monitor_work_area(self, x: int, y: int) -> tuple[int, int, int, int]:
        if os.name != "nt":
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong), ("rcMonitor", Rect),
                ("rcWork", Rect), ("dwFlags", ctypes.c_ulong),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(Point(x, y), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            return work.left, work.top, work.right, work.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    @staticmethod
    def _clamp_position(
        x: int, y: int, width: int, height: int, bounds: tuple[int, int, int, int]
    ) -> tuple[int, int]:
        left, top, right, bottom = bounds
        return (
            min(max(x, left), max(left, right - width)),
            min(max(y, top), max(top, bottom - height)),
        )

    def _drag_start(self, event: tk.Event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        x = event.x_root - self.drag_origin[0]
        y = event.y_root - self.drag_origin[1]
        width, height = self.root.winfo_width(), self.root.winfo_height()
        bounds = self._monitor_work_area(event.x_root, event.y_root)
        x, y = self._clamp_position(x, y, width, height, bounds)
        self._set_geometry(width, height, x, y)

    def expand(self) -> None:
        self._show_panel()
        self._render()

    def collapse(self) -> None:
        self._show_button()

    def _clear_panel(self) -> None:
        for child in self.panel.winfo_children():
            child.destroy()

    def _label(self, parent: tk.Widget, text: str, size: int = 9, color: str = INK,
               weight: str = "normal", **kwargs: Any) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=parent.cget("bg"), fg=color,
            font=(FONT, size, weight), anchor="w", **kwargs,
        )

    def _icon_button(self, parent: tk.Widget, text: str, command: Any) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=SURFACE, fg=MUTED,
            activebackground=BG, activeforeground=INK, relief="flat", bd=0,
            width=3, height=1, cursor="hand2", font=(FONT, 10, "bold"),
        )

    def _status_color(self, sessions: list[dict[str, Any]]) -> str:
        if not sessions:
            return GREEN
        levels = {str(item.get("level") or "normal") for item in sessions}
        if "critical" in levels:
            return RED
        if "warning" in levels:
            return AMBER
        return GREEN

    def _render(self) -> None:
        if not self.expanded:
            return
        self._clear_panel()
        sessions = self.payload.get("active_sessions", [])
        summary = self.payload.get("summary", {})
        config = self.payload.get("config", {})
        accent = self._status_color(sessions)
        height = min(650, 275 + 145 * max(1, len(sessions)))
        bounds = self._monitor_work_area(self.root.winfo_x() + 215, self.root.winfo_y() + 24)
        x, y = self._clamp_position(
            self.root.winfo_x(), self.root.winfo_y(), 430, height, bounds
        )
        self._set_geometry(430, height, x, y)

        header = tk.Frame(self.panel, bg=SURFACE, height=54)
        header.pack(fill="x", padx=18, pady=(10, 0))
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self._drag_start)
        header.bind("<B1-Motion>", self._drag_move)
        dot = tk.Canvas(header, width=12, height=12, bg=SURFACE, highlightthickness=0)
        dot.create_oval(2, 2, 10, 10, fill=accent, outline=accent)
        dot.pack(side="left", padx=(0, 9))
        title_box = tk.Frame(header, bg=SURFACE)
        title_box.pack(side="left", fill="y")
        self._label(title_box, "Context Tree", 11, INK, "bold").pack(anchor="w")
        subtitle = f"{len(sessions)} 个会话正在处理" if sessions else "当前空闲"
        self._label(title_box, subtitle, 8, MUTED).pack(anchor="w", pady=(2, 0))
        self._icon_button(header, "_", self.collapse).pack(side="right")

        rule = tk.Frame(self.panel, bg=LINE, height=1)
        rule.pack(fill="x")

        overview = tk.Frame(self.panel, bg=BG, height=64)
        overview.pack(fill="x")
        overview.pack_propagate(False)
        processing = int(summary.get("processing_count", 0) or 0)
        stats = []
        if processing:
            stats.append(("AI 整理中", str(processing)))
        stats.extend([
            ("可整理", f"{summary.get('ready_count', summary.get('pending_count', 0))} / {config.get('consolidate_every', 3)}"),
            ("记录中", str(summary.get("recording_count", 0))),
            ("长期主题", str(summary.get("topic_count", 0))),
            ("活动会话", str(len(sessions))),
        ])
        for index, (label, value) in enumerate(stats):
            box = tk.Frame(overview, bg=BG)
            box.pack(side="left", fill="both", expand=True, padx=(18 if index == 0 else 8, 8))
            self._label(box, value, 12, INK, "bold").pack(anchor="w", pady=(10, 0))
            self._label(box, label, 8, MUTED).pack(anchor="w", pady=(2, 0))

        list_shell = tk.Frame(self.panel, bg=SURFACE)
        list_shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(list_shell, bg=SURFACE, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_shell, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=SURFACE)
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units")
        ))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        canvas.bind("<ButtonPress-2>", lambda event: canvas.scan_mark(event.x, event.y))
        canvas.bind("<B2-Motion>", lambda event: canvas.scan_dragto(event.x, event.y, gain=1))

        for index, session in enumerate(sessions):
            if index:
                tk.Frame(content, bg=LINE, height=1).pack(fill="x", padx=18)
            self._render_session(content, session)

        if not sessions:
            empty = tk.Frame(content, bg=SURFACE)
            empty.pack(fill="x", padx=18, pady=(24, 28))
            self._label(empty, "当前没有正在处理的会话", 10, INK, "bold").pack(anchor="w")
            self._label(empty, "等待下一次请求", 8, MUTED).pack(anchor="w", pady=(5, 0))

        footer = tk.Frame(content, bg=SURFACE)
        footer.pack(fill="x", padx=18, pady=(0, 14))
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, 10))
        self._action(footer, "打开记忆树", self.open_graph).pack(side="left", padx=(0, 6))
        self._action(footer, "设置", self.open_settings).pack(side="left", padx=6)
        usage = self.payload.get("usage", {})
        source = "服务端数据" if usage.get("mode") == "real" else (
            "本地回退" if usage.get("error") else "Codex 本地"
        )
        self._label(footer, source, 8, accent, "bold").pack(side="right")

    def _action(self, parent: tk.Widget, text: str, command: Any) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=BG, fg=INK,
            activebackground=LINE, activeforeground=INK, relief="flat", bd=0,
            padx=12, pady=5, cursor="hand2", font=(FONT, 8, "normal"),
        )

    def _render_session(self, parent: tk.Widget, session: dict[str, Any]) -> None:
        block = tk.Frame(parent, bg=SURFACE)
        block.pack(fill="x", padx=18, pady=15)
        title = str(session.get("session_title") or session.get("session_id") or "未命名会话")
        self._label(block, title, 10, INK, "bold", wraplength=370, justify="left").pack(fill="x")
        topic = str(session.get("topic_title") or "待自动归类")
        sid = str(session.get("session_id") or "")
        slot = session.get("window_slot")
        window_text = f"  |  窗口 {slot}" if slot not in {None, ""} else ""
        self._label(block, f"{topic}  |  {sid[:8]}{window_text}", 8, MUTED).pack(fill="x", pady=(4, 10))

        capacity = number(
            session.get("capacity_tokens"),
            number(self.payload.get("usage", {}).get("capacity_tokens"), 272000),
        )
        has_tokens = session.get("context_tokens") is not None
        tokens = number(session.get("context_tokens"))
        percent = number(session.get("percent"), tokens / max(1, capacity) * 100)
        level = str(session.get("level") or "normal")
        accent = RED if level == "critical" else AMBER if level == "warning" else GREEN
        track = tk.Canvas(block, height=7, bg=SURFACE, highlightthickness=0)
        track.pack(fill="x")
        track.bind("<Configure>", lambda e, p=percent, a=accent, c=track: self._draw_track(c, e.width, p, a))

        first_token = session.get("first_token_ms") or session.get("latest_first_token_ms")
        cost = session.get("recent_cost") or session.get("total_cost")
        processing = int(session.get("processing_count") or 0)
        pending = int(session.get("ready_count", session.get("pending_count", 0)) or 0)
        recording = int(session.get("recording_count") or 0)
        session_metric = (
            "本会话整理", f"{processing} 整理中"
        ) if processing else ("可整理 / 记录中", f"{pending} / {recording}")
        metrics = [
            ("窗口占用", f"{compact_number(tokens)}  {percent:.0f}%" if has_tokens else "--"),
            ("首字", format_seconds(first_token) if first_token is not None else "--"),
            session_metric,
            ("已压缩", f"{session.get('compaction_count') or 0} 次"),
        ]
        row = tk.Frame(block, bg=SURFACE)
        row.pack(fill="x", pady=(10, 0))
        for label, value in metrics:
            cell = tk.Frame(row, bg=SURFACE)
            cell.pack(side="left", fill="x", expand=True)
            self._label(cell, value, 8, INK, "bold").pack(anchor="w")
            self._label(cell, label, 7, MUTED).pack(anchor="w", pady=(2, 0))
        request_count = int(session.get("turn_cost_request_count") or session.get("turn_request_count") or 0)
        cost_profile_status = str(session.get("cost_profile_status") or "")
        if cost_profile_status in {"ready", "partial_history"}:
            epoch_index = int(session.get("current_epoch_index") or 0)
            epoch_label = "压缩前" if epoch_index == 0 else f"第 {epoch_index} 次压缩后"
            cost_text = (
                f"本轮 {request_count} 次  |  平均计费 ${number(session.get('turn_avg_cost')):.4f}  |  "
                f"上下文 ${number(session.get('turn_avg_context_cost')):.4f}\n"
                f"API 首字 {format_seconds(session.get('turn_avg_first_token_ms'))}  |  "
                f"API 耗时 {format_seconds(session.get('turn_avg_duration_ms'))}\n"
            )
            if cost_profile_status == "partial_history":
                reference_label = str(session.get("reference_epoch_label") or "最早记录")
                extra = number(session.get("reference_epoch_extra_avg_cost"))
                cost_text += (
                    f"压缩前无服务端记录  |  {reference_label} "
                    f"${number(session.get('reference_avg_cost')):.4f}  →  "
                    f"{epoch_label} ${number(session.get('current_epoch_avg_cost')):.4f}  |  "
                    f"{extra:+.4f} 美元/次"
                )
            else:
                extra = number(session.get("current_epoch_extra_avg_cost"))
                cost_text += (
                    f"压缩前 ${number(session.get('baseline_avg_cost')):.4f}  →  "
                    f"{epoch_label} ${number(session.get('current_epoch_avg_cost')):.4f}  |  "
                    f"{extra:+.4f} 美元/次"
                )
        elif request_count:
            average_percent = session.get("turn_avg_window_percent")
            cache_percent = session.get("turn_cache_hit_percent")
            cost_text = f"本轮 {request_count} 次"
            if average_percent is not None:
                cost_text += f"  |  平均占用 {number(average_percent):.1f}%"
            if cache_percent is not None:
                cost_text += f"  |  缓存 {number(cache_percent):.1f}%"
            epochs = session.get("epoch_summaries") if isinstance(session.get("epoch_summaries"), list) else []
            current_index = int(session.get("current_epoch_index") or 0)
            baseline = epochs[0] if epochs else None
            current = next((item for item in epochs if int(item.get("epoch_index") or 0) == current_index), epochs[-1] if epochs else None)
            if baseline and current:
                cost_text += (
                    f"\n{baseline.get('label', '压缩前')} {int(baseline.get('request_count') or 0)} 次  →  "
                    f"{current.get('label', f'第{current_index}次压缩后')} {int(current.get('request_count') or 0)} 次"
                    "  |  费用待服务端"
                )
            else:
                cost_text += "  |  费用待服务端"
        else:
            cost_text = ""
        if cost_text:
            self._label(block, cost_text, 7, MUTED, wraplength=380, justify="left").pack(anchor="w", pady=(8, 0))
        epochs = session.get("epoch_summaries") if isinstance(session.get("epoch_summaries"), list) else []
        if epochs:
            epoch_key = str(session.get("session_id") or "")
            expanded = epoch_key in self.expanded_epochs
            self._action(
                block,
                f"{'收起' if expanded else '展开'}压缩阶段 ({len(epochs)})",
                lambda key=epoch_key: self._toggle_epochs(key),
            ).pack(anchor="w", pady=(9, 0))
            if expanded:
                self._render_epoch_history(block, session)
        if cost is not None:
            self._label(block, f"最近 5 次成本  ${number(cost):.4f}", 7, MUTED).pack(anchor="w", pady=(9, 0))
        attention = str(session.get("attention_text") or "")
        if attention:
            self._label(block, attention, 8, accent, "bold").pack(anchor="w", pady=(7, 0))

    def _toggle_epochs(self, session_id: str) -> None:
        if session_id in self.expanded_epochs:
            self.expanded_epochs.remove(session_id)
        else:
            self.expanded_epochs.add(session_id)
        self._render()

    def _render_epoch_history(self, parent: tk.Widget, session: dict[str, Any]) -> None:
        epochs = session.get("epoch_summaries") if isinstance(session.get("epoch_summaries"), list) else []
        if not epochs:
            return
        local_epochs = {
            int(item.get("epoch_index") or 0): item
            for item in session.get("local_epoch_summaries", [])
            if isinstance(item, dict)
        }
        self._label(parent, f"压缩阶段  {len(epochs)} 个", 8, INK, "bold").pack(anchor="w", pady=(12, 5))
        history = tk.Frame(parent, bg=BG)
        history.pack(fill="x")
        for position, epoch in enumerate(epochs):
            index = int(epoch.get("epoch_index") or 0)
            local = local_epochs.get(index, {})
            row = tk.Frame(history, bg=BG)
            row.pack(fill="x", padx=10, pady=(6 if position == 0 else 3, 6 if position == len(epochs) - 1 else 3))
            label = str(epoch.get("label") or ("压缩前" if index == 0 else f"第{index}次压缩后"))
            request_count = int(epoch.get("request_count") or 0)
            self._label(row, label, 7, INK, "bold").pack(anchor="w")
            if epoch.get("avg_cost") is not None:
                detail = (
                    f"服务端 {request_count} 次  |  ${number(epoch.get('avg_cost')):.4f}/次  |  "
                    f"上下文 ${number(epoch.get('avg_context_cost')):.4f}\n"
                    f"首字 {format_seconds(epoch.get('avg_first_token_ms'))}  |  "
                    f"耗时 {format_seconds(epoch.get('avg_duration_ms'))}"
                )
            else:
                local_count = int(local.get("request_count") or 0)
                detail = f"费用未记录  |  本地 {local_count} 次"
                if local.get("avg_context_tokens") is not None:
                    detail += f"  |  平均上下文 {compact_number(local.get('avg_context_tokens'))}"
                if local.get("avg_window_percent") is not None:
                    detail += f"  |  占用 {number(local.get('avg_window_percent')):.1f}%"
            self._label(row, detail, 7, MUTED, wraplength=350, justify="left").pack(anchor="w", pady=(2, 0))

    @staticmethod
    def _draw_track(canvas: tk.Canvas, width: int, percent: float, accent: str) -> None:
        canvas.delete("all")
        canvas.create_rectangle(0, 1, width, 6, fill=TRACK, outline=TRACK)
        canvas.create_rectangle(0, 1, max(4, width * min(100, percent) / 100), 6, fill=accent, outline=accent)

    def open_graph(self) -> None:
        topics = self.payload.get("topics", [])
        sessions = self.payload.get("active_sessions", [])
        topic_id = next((item.get("topic_id") for item in sessions if item.get("topic_id")), None)
        if not topic_id and topics:
            topic_id = topics[0].get("id")
        cmd = [sys.executable, str(ROOT / "scripts" / "context_tree.py"), "--store", str(STORE), "ui"]
        if topic_id:
            cmd.extend(["--topic", str(topic_id)])
        subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def open_settings(self) -> None:
        cmd = [sys.executable, str(ROOT / "scripts" / "context_tree.py"), "--store", str(STORE), "ui"]
        subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        threading.Thread(target=self._load_payload, name="context-tree-refresh", daemon=True).start()

    def _load_payload(self) -> None:
        db = None
        try:
            db = ct.initialize_store(STORE)
            payload = ct.settings_payload(db, STORE)
            self._refresh_results.put((payload, None))
        except Exception as error:
            self._refresh_results.put((None, error))
        finally:
            if db is not None:
                db.close()

    def _poll_refresh(self) -> None:
        try:
            payload, _error = self._refresh_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._refresh_in_flight = False
            if payload is not None:
                self._apply_payload(payload)
        self.root.after(100, self._poll_refresh)

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        sessions = self.payload.get("active_sessions", [])
        persistent = bool(self.payload.get("config", {}).get("float_persistent", False))
        signature = repr((
            self.payload.get("summary", {}).get("pending_count"),
            persistent,
            [(item.get("session_id"), item.get("session_title"), item.get("context_tokens"),
              item.get("compaction_count"), item.get("pending_count"),
              item.get("processing_count"), item.get("first_token_ms"),
              item.get("request_count"), item.get("level")) for item in sessions],
        ))
        if not should_show_float(self.payload):
            self.root.withdraw()
            self.visible = False
            return
        if not self.visible:
            if not sessions:
                self._show_button()
            self.root.deiconify()
            self.visible = True
        accent = self._status_color(sessions)
        self.collapsed.configure(bg=accent, activebackground=accent)
        if signature != self.session_signature:
            self.session_signature = signature
            self._render()

    def _tick(self) -> None:
        if self.follow_codex:
            if codex_desktop_running():
                self.codex_missing_since = None
            elif self.codex_missing_since is None:
                self.codex_missing_since = time.monotonic()
            elif time.monotonic() - self.codex_missing_since >= 8:
                self.root.destroy()
                return
        self.refresh()
        self.root.after(EXPANDED_REFRESH_MS if self.expanded else COLLAPSED_REFRESH_MS, self._tick)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    if acquire_single_instance():
        ContextTreeFloat().run()
