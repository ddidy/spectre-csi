#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 SPECTRE-CSI :: Wi-Fi Channel State Information Presence & Motion Detector
================================================================================

 Standalone, production-oriented CLI tool for human presence / motion sensing
 via Wi-Fi CSI amplitude variation. Cyberpunk terminal UI built with `rich`,
 signal processing with `numpy`, optional ESP32 serial link via `pyserial`.

 SETUP (Linux / Termux):
   pip install rich numpy pyserial

 USAGE:
   python spectre_csi.py --demo                # synthetic CSI, no hardware
   python spectre_csi.py --port /dev/ttyUSB0   # live ESP32 stream
   python spectre_csi.py --port /dev/ttyUSB0 --baud 921600 --csv session.csv

 ESP32 FIRMWARE PROTOCOL (serial text mode):
   One frame per line, comma-separated subcarrier amplitudes, e.g.
       CSI_DATA:512,498,501,...
   Any line containing >= 8 comma-separated integers is accepted.

 AUTHOR : Senior Embedded / Python Engineer
 LICENSE: MIT-style, do whatever, blame nobody.
================================================================================
"""

import argparse
import csv
import math
import os
import re
import sys
import time
import random
import datetime

import numpy as np

# ----------------------------------------------------------------------------
# Optional pyserial — tool degrades gracefully if absent
# ----------------------------------------------------------------------------
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.box import ROUNDED
from rich import box

# ----------------------------------------------------------------------------
# Constants & Aesthetics
# ----------------------------------------------------------------------------
TOOL_NAME = "SPECTRE-CSI"
VERSION = "1.0.0"

BANNER = r"""
███████╗██████╗ ███████╗ ██████╗████████╗██████╗ ███████╗       ██████╗███████╗██╗
██╔════╝██╔══██╗██╔════╝██╔════╝╚══██╔══╝██╔══██╗██╔════╝      ██╔════╝██╔════╝██║
███████╗██████╔╝█████╗  ██║        ██║   ██████╔╝█████╗  █████╗██║     ███████╗██║
╚════██║██╔═══╝ ██╔══╝  ██║        ██║   ██╔══██╗██╔══╝  ╚════╝██║     ╚════██║██║
███████║██║     ███████╗╚██████╗   ██║   ██║  ██║███████╗      ╚██████╗███████║██║
╚══════╝╚═╝     ╚══════╝ ╚═════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝       ╚═════╝╚══════╝╚═╝
        W i - F i   C h a n n e l   S t a t e   I n f o r m a t i o n   S e n s o r
"""

WAVE_BLOCKS = " ▁▂▃▄▅▆▇█"          # 10-level ASCII/Unicode waveform ramp
SUBCARRIER_BAR = "█"
NSUB = 64                            # default subcarrier count
HISTORY_LEN = 96                     # frames kept for envelope waveform
RECONNECT_DELAY = 2.0                # seconds between serial retries

# Detection thresholds (tuned for demo & typical CSI amplitude scale)
TH_MINOR = 0.55
TH_HEAVY = 1.60

console = Console()


# ----------------------------------------------------------------------------
# Detection state machine
# ----------------------------------------------------------------------------
class PresenceState:
    CLEAR = 0
    MINOR = 1       # breathing / subtle motion
    HEAVY = 2       # strong motion

    LABELS = {
        CLEAR: ("CLEAR / STILL", "green"),
        MINOR: ("MINOR MOTION", "yellow"),
        HEAVY: ("HEAVY MOTION", "red"),
    }


# ----------------------------------------------------------------------------
# CSI Frame abstraction
# ----------------------------------------------------------------------------
class CSIFrame:
    """Single CSI snapshot: amplitudes per subcarrier."""

    __slots__ = ("timestamp", "amplitudes", "subcarrier_count")

    def __init__(self, amplitudes):
        self.timestamp = time.time()
        a = np.asarray(amplitudes, dtype=np.float64)
        self.amplitudes = a
        self.subcarrier_count = a.size


# ----------------------------------------------------------------------------
# Synthetic CSI generator (demo mode)
# ----------------------------------------------------------------------------
class SyntheticCSI:
    """
    Generates realistic Wi-Fi CSI amplitude frames:
      - static multipath baseline (per-subcarrier random complex gain)
      - slow breathing modulation when 'person present'
      - occasional motion bursts (walking, gestures) with heavy spikes
    """

    def __init__(self, nsub=NSUB, seed=None):
        self.rng = np.random.default_rng(seed)
        self.nsub = nsub
        self.t = 0.0
        self.presence = 0.0            # 0..1 occupancy factor
        self._target_presence = 0.0
        self.burst = 0.0               # active motion burst envelope
        self._burst_timer = 0.0
        # Static per-subcarrier channel response (multipath fingerprint)
        self.h_static = (
            self.rng.normal(0, 1, nsub) + 1j * self.rng.normal(0, 1, nsub)
        )
        self.h_mag = np.abs(self.h_static)

    def _update_scene(self, dt):
        self.t += dt
        # Scene changes every ~8-15 s: somebody enters/leaves
        self._burst_timer -= dt
        if self._burst_timer <= 0:
            self._burst_timer = self.rng.uniform(6.0, 14.0)
            self._target_presence = self.rng.choice([0.0, 0.0, 0.6, 1.0])
        self.presence += (self._target_presence - self.presence) * dt * 0.8
        # Motion bursts: short-lived spikes when occupied
        if self.presence > 0.4 and self.rng.random() < dt * 0.9:
            self.burst = self.rng.uniform(0.6, 1.0)
        self.burst *= math.exp(-dt * 2.2)   # exponential decay

    def read_frame(self, dt=0.05):
        self._update_scene(dt)
        n = self.nsub
        idx = np.arange(n)
        # Breathing: low-frequency spatial ripple across subcarriers
        breath = self.presence * 6.0 * np.sin(
            2 * math.pi * 0.25 * self.t + idx * 0.12
        )
        # Motion: jitter + broadband ripple, heavier during bursts
        motion_amp = self.presence * (0.4 + 2.6 * self.burst)
        motion = motion_amp * self.rng.normal(0, 1, n) * (
            0.6 + 0.4 * np.sin(idx * 0.5 + self.t * 7.0)
        )
        # Thermal / measurement noise
        noise = self.rng.normal(0, 0.9, n)
        # Slow thermal drift of the whole channel
        drift = 3.0 * math.sin(2 * math.pi * 0.02 * self.t)
        amp = self.h_mag * 28.0 + drift + breath + motion + noise
        return CSIFrame(np.clip(amp, 0, None))


# ----------------------------------------------------------------------------
# Serial CSI reader with auto-reconnect
# ----------------------------------------------------------------------------
class SerialCSIReader:
    """
    Robust line-oriented serial listener.
    Accepts frames like:  CSI_DATA:v1,v2,v3,...
    Falls back: any line with >= 8 comma-separated integers.
    """

    NUM_RE = re.compile(r"-?\d+")

    def __init__(self, port, baud):
        self.port_name = port
        self.baud = baud
        self.ser = None
        self.connected = False
        self.last_error = "never attempted"
        self.frames_read = 0

    def _open(self):
        try:
            self.ser = serial.Serial(
                self.port_name, self.baud, timeout=0.2,
                write_timeout=1, dsrdtr=False, rtscts=False,
            )
            self.connected = True
            self.last_error = ""
        except Exception as exc:                       # noqa: BLE001
            self.connected = False
            self.last_error = str(exc)
            self.ser = None

    def connect(self):
        self._open()
        return self.connected

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:                          # noqa: BLE001
                pass
        self.connected = False

    def read_frame(self, dt=0.05):
        """Non-blocking-ish frame pump; returns CSIFrame or None."""
        if not self.connected:
            time.sleep(dt)
            self._open()                               # auto-reconnect
            return None
        try:
            raw = self.ser.readline()
        except Exception as exc:                       # noqa: BLE001
            self.last_error = str(exc)
            self.close()
            return None
        if not raw:
            return None
        try:
            line = raw.decode("ascii", errors="ignore").strip()
        except Exception:                              # noqa: BLE001
            return None
        if not line:
            return None
        # Prefer numbers after a known marker; else any number run in line
        payload = line
        m = re.search(r"CSI[_A-Z]*[: ,](.+)$", line, re.IGNORECASE)
        if m:
            payload = m.group(1)
        vals = self.NUM_RE.findall(payload)
        if len(vals) < 8:
            return None
        try:
            amps = [float(v) for v in vals[:256]]      # cap frame length
        except ValueError:
            return None
        self.frames_read += 1
        return CSIFrame(amps)


# ----------------------------------------------------------------------------
# Signal processing core
# ----------------------------------------------------------------------------
class SignalProcessor:
    """
    Extracts a motion score from a rolling CSI history:
      1. per-frame envelope = mean |amplitude|
      2. detrend with a moving average
      3. motion score = robust std (MAD-scaled) of residual, EMA-smoothed
    """

    def __init__(self, history=HISTORY_LEN):
        self.history = history
        self.envelope = np.zeros(history)
        self.n = 0
        self.score_ema = 0.0
        self.ema_alpha = 0.25

    def update(self, frame: CSIFrame):
        env = float(np.mean(frame.amplitudes))
        self.envelope = np.roll(self.envelope, -1)
        self.envelope[-1] = env
        self.n = min(self.n + 1, self.history)
        return self.compute_score()

    def compute_score(self):
        if self.n < 16:
            return 0.0, 0.0
        win = self.envelope[-self.n:]
        k = max(5, self.n // 4)
        kernel = np.ones(k) / k
        trend = np.convolve(win, kernel, mode="same")
        resid = win - trend
        mad = np.median(np.abs(resid - np.median(resid)))
        score = 1.4826 * mad          # robust std estimate
        self.score_ema += self.ema_alpha * (score - self.score_ema)
        return float(self.score_ema), float(np.std(resid))

    @staticmethod
    def classify(score):
        if score >= TH_HEAVY:
            return PresenceState.HEAVY
        if score >= TH_MINOR:
            return PresenceState.MINOR
        return PresenceState.CLEAR


# ----------------------------------------------------------------------------
# Rendering helpers
# ----------------------------------------------------------------------------
def badge(state):
    label, color = PresenceState.LABELS[state]
    return Text(f" {label} ", style=f"bold black on {color}")


def render_waveform(values, width=HISTORY_LEN):
    """Map a 1-D series onto the block-character ramp."""
    if values.size == 0:
        return ""
    lo, hi = float(np.min(values)), float(np.max(values))
    span = (hi - lo) or 1.0
    norm = (values - lo) / span
    idx = np.clip((norm * (len(WAVE_BLOCKS) - 1)).astype(int), 0, len(WAVE_BLOCKS) - 1)
    return "".join(WAVE_BLOCKS[i] for i in idx[-width:])


def render_subcarrier_profile(frame: CSIFrame, width=48):
    if frame is None:
        return ""
    a = frame.amplitudes
    if a.size > width:                     # decimate to fit
        step = a.size / width
        a = np.array([a[int(i * step)] for i in range(width)])
    lo, hi = float(a.min()), float(a.max())
    span = (hi - lo) or 1.0
    norm = (a - lo) / span
    idx = np.clip((norm * (len(WAVE_BLOCKS) - 1)).astype(int), 0, len(WAVE_BLOCKS) - 1)
    return "".join(WAVE_BLOCKS[i] for i in idx)


def sparkline_stats(proc: SignalProcessor):
    if proc.n < 2:
        return "warmup: collecting frames…"
    return (
        f"frames={proc.n:>3}  "
        f"env_min={proc.envelope.min():7.1f}  "
        f"env_max={proc.envelope.max():7.1f}  "
        f"env_now={proc.envelope[-1]:7.1f}"
    )


def build_ui(state, score, proc, frame, source_info, csv_path, frames_total, uptime):
    """Assemble the full cyberpunk dashboard."""
    head = Text(BANNER, style="bold magenta")

    # --- main status table ---
    tbl = Table(box=ROUNDED, expand=True, title="[bold cyan]LIVE TELEMETRY",
                title_justify="center", header_style="bold cyan",
                row_styles=["on #0d0d1a", "on #111122"])
    tbl.add_column("FIELD", style="bold white", no_wrap=True)
    tbl.add_column("VALUE", style="bright_white")

    label, color = PresenceState.LABELS[state]
    tbl.add_row("STATUS", badge(state))
    tbl.add_row("MOTION SCORE", f"[bold {color}]{score:6.3f}[/]  "
                                f"(minor ≥ {TH_MINOR}, heavy ≥ {TH_HEAVY})")
    tbl.add_row("SUBCARRIERS", f"{frame.subcarrier_count if frame else 0}")
    tbl.add_row("SOURCE", source_info)
    tbl.add_row("SESSION", f"{frames_total} frames | uptime {uptime}")
    tbl.add_row("CSV LOG", csv_path if csv_path else "[dim]disabled[/]")
    tbl.add_row("THRESHOLDS", f"[yellow]MINOR {TH_MINOR}[/] / [red]HEAVY {TH_HEAVY}[/]")

    # --- waveform panel ---
    wave = render_waveform(proc.envelope if proc.n > 0 else np.zeros(1))
    wave_panel = Panel(
        f"[bold bright_cyan]{wave}[/]\n[dim]{sparkline_stats(proc)}[/]",
        title="[bold cyan]AMPLITUDE ENVELOPE — MOTION WAVEFORM[/]",
        box=ROUNDED, border_style="cyan", padding=(1, 2),
    )

    # --- subcarrier profile panel ---
    profile = render_subcarrier_profile(frame) if frame else ""
    prof_panel = Panel(
        f"[bold bright_magenta]{profile}[/]\n"
        f"[dim]per-subcarrier amplitude profile (current frame)[/]",
        title="[bold magenta]SUBCARRIER SNAPSHOT[/]",
        box=ROUNDED, border_style="magenta", padding=(1, 2),
    )

    legend = (
        "[green]● CLEAR[/]   channel static — no presence\n"
        "[yellow]● MINOR[/]  breathing / micro-motion\n"
        "[red]● HEAVY[/]   walking / large movement"
    )
    foot = Panel(legend, box=ROUNDED, border_style="bright_black",
                 title="[bold bright_black]LEGEND[/]", padding=(0, 2))

    return Group(head, tbl, wave_panel, prof_panel, foot)


# ----------------------------------------------------------------------------
# Session CSV recorder
# ----------------------------------------------------------------------------
class CsvRecorder:
    def __init__(self, path):
        self.path = path
        self.fh = open(path, "w", newline="", encoding="utf-8")   # noqa: SIM115
        self.writer = csv.writer(self.fh)
        self.writer.writerow([
            "timestamp_iso", "epoch", "motion_score", "state",
            "subcarrier_count", "envelope_mean",
        ])

    def write(self, frame, score, state):
        self.writer.writerow([
            datetime.datetime.now().isoformat(timespec="milliseconds"),
            f"{frame.timestamp:.3f}", f"{score:.4f}",
            PresenceState.LABELS[state][0], frame.subcarrier_count,
            f"{float(np.mean(frame.amplitudes)):.3f}",
        ])

    def flush(self):
        self.fh.flush()

    def close(self):
        try:
            self.fh.close()
        except Exception:                          # noqa: BLE001
            pass


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        prog=TOOL_NAME.lower(),
        description="Wi-Fi CSI human presence & motion detector (standalone).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--demo", action="store_true",
                   help="run with synthetic CSI data (no hardware required)")
    p.add_argument("--port", metavar="DEVICE", default=None,
                   help="serial device, e.g. /dev/ttyUSB0 (enables live mode)")
    p.add_argument("--baud", type=int, default=115200,
                   help="serial baud rate")
    p.add_argument("--csv", metavar="FILE", default=None,
                   help="optional CSV session recording path")
    return p.parse_args()


def main():
    args = parse_args()

    if not args.demo and not args.port:
        console.print("[red]✖[/] Either --demo or --port DEVICE is required. "
                      "Try: [bold cyan]python spectre_csi.py --demo[/]")
        sys.exit(2)

    if args.port and not SERIAL_AVAILABLE:
        console.print("[red]✖[/] pyserial is required for live mode: "
                      "[bold]pip install pyserial[/]")
        sys.exit(2)

    if args.port and not os.path.exists(args.port):
        console.print(f"[yellow]⚠[/] Device [bold]{args.port}[/] not found yet — "
                      "will keep retrying in background.")

    # --- source setup ---
    if args.demo:
        source = SyntheticCSI(nsub=NSUB)
        source_info = f"[bold yellow]SIMULATION[/] synthetic subcarriers ×{NSUB}"
    else:
        source = SerialCSIReader(args.port, args.baud)
        source_info = f"[bold green]SERIAL[/] {args.port} @ {args.baud} baud"

    recorder = CsvRecorder(args.csv) if args.csv else None
    proc = SignalProcessor()
    frames_total = 0
    start_time = time.time()
    state = PresenceState.CLEAR
    last_frame = None
    last_ui = 0.0
    UI_REFRESH = 0.08          # ~12 fps terminal refresh
    SAMPLE_DT = 0.05

    console.clear()
    console.print(Text(BANNER, style="bold magenta"))
    console.print(Panel.fit(
        f"[bold]{TOOL_NAME} v{VERSION}[/] — standalone CSI sensing engine\n"
        f"mode: {'DEMO' if args.demo else 'LIVE SERIAL'} | "
        f"logging: {args.csv or 'off'}",
        box=ROUNDED, border_style="bright_black"))
    console.print("[dim]Press Ctrl-C to exit.[/]\n")
    time.sleep(0.8)

    try:
        with Live(build_ui(state, 0.0, proc, None, source_info,
                           args.csv, 0, "0s"),
                  console=console, refresh_per_second=12,
                  screen=True, vertical_overflow="visible") as live:

            while True:
                # --- acquire frame ---
                if args.demo:
                    frame = source.read_frame(dt=SAMPLE_DT)
                    time.sleep(SAMPLE_DT)
                else:
                    frame = source.read_frame(dt=SAMPLE_DT)
                    if frame is None and not source.connected:
                        source_info = (
                            f"[bold red]RECONNECTING[/] {args.port} "
                            f"([dim]{source.last_error or 'no data'}[/])"
                        )
                    elif source.connected:
                        source_info = (f"[bold green]SERIAL[/] {args.port} "
                                       f"@ {args.baud} baud")

                # --- process ---
                if frame is not None:
                    last_frame = frame
                    score, _raw = proc.update(frame)
                    state = proc.classify(score)
                    frames_total += 1
                    if recorder:
                        recorder.write(frame, score, state)

                # --- render (throttled) ---
                now = time.time()
                if now - last_ui >= UI_REFRESH:
                    uptime = f"{int(now - start_time)}s"
                    live.update(build_ui(
                        state,
                        proc.score_ema if frame is not None else proc.score_ema,
                        proc, last_frame, source_info, args.csv,
                        frames_total, uptime))
                    last_ui = now

                if recorder and frames_total % 10 == 0:
                    recorder.flush()

    except KeyboardInterrupt:
        pass
    finally:
        if recorder:
            recorder.close()
        if isinstance(source, SerialCSIReader):
            source.close()
        console.print(
            f"\n[bold green]✔ SESSION ENDED[/] — {frames_total} frames "
            f"processed in {int(time.time() - start_time)}s. Stay stealthy.\n")


if __name__ == "__main__":
    main()
