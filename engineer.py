#!/usr/bin/env python3
"""
engineer.py — race engineer for EA F1 25/26 (Xbox), running on a Mac.

What it does, with no API key and no cost:
  * speaks the important stuff over your Mac's speakers as it happens — lap time and delta to your best,
    which sector you lost it in, safety car, flags, penalties, pit window, fuel, tyre wear, damage
    (--no-voice turns the radio off completely: no speech, no chatter in the terminal)
  * records every session to a folder in  sessions/  (lap times + sectors, tyre/fuel per lap, and a
    20 Hz trace of speed/throttle/brake/steer/gear by distance) so you can drop the files into Claude
    afterwards for a debrief
  * serves a live dashboard at http://localhost:8000 (dashboard.html must sit next to this file) —
    graphs redraw as you drive

If an ANTHROPIC_API_KEY is in the .env file it also becomes a talking engineer (tap SPACE, ask, get a
spoken answer). Without a key that part is simply off.

Modes
  uv run engineer.py --test       telemetry check: shows live data from the game
  uv run engineer.py              engineer: callouts + session recording (+ voice questions if a key is set)
  uv run engineer.py --text       same, but type questions instead of speaking them (needs a key)
  uv run engineer.py --mic-test   records 4 seconds, transcribes, reads it back (needs the speech model)

Keys while it is running
  S                 spoken status (position, gaps, tyres, fuel)
  L                 read out the last lap again
  SPACE             start / stop talking (only with an API key)
  Q                 quit and save the session (Ctrl+C does the same)
"""

from __future__ import annotations

import argparse
import csv
import http.server
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:                                  # single-key input: Unix first, Windows fallback
    import termios
    import tty
    _MSVCRT = None
except ImportError:                   # pragma: no cover - Windows
    termios = tty = None              # type: ignore[assignment]
    import msvcrt as _MSVCRT          # type: ignore[no-redef]

from opponent_traces import OpponentTraces
from coaching import CoachingService, load_json
from setup_baseline import SetupBaselineService

from f1_udp import (RaceState, TelemetryListener, DEFAULT_PORT, INFRINGEMENTS, PENALTY_TYPES,
                    TRACKS, SESSION_TYPES, WEATHER, SAFETY_CAR, FLAGS, VISUAL_COMPOUND, ACTUAL_COMPOUND,
                    FUEL_MIX, ERS_MODE, DRIVER_STATUS, TEAMS, PKT_LAP, PKT_TELEMETRY, PKT_SETUPS, PKT_EVENT, PKT_HISTORY,
                    fmt_lap, fmt_lap_spoken, sector_ms)

def load_env(path: Path | None = None) -> None:
    """Read simple KEY=value lines from the .env file next to this script, if there is one."""
    path = path or Path(__file__).resolve().parent / ".env"
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env()

# ----------------------------------------------------------------------------- SETTINGS
# Every one of these can be overridden in .env, e.g.  ENGINEER_VOICE=Daniel
MODEL = os.environ.get("ENGINEER_MODEL", "claude-sonnet-5")          # Claude model for the radio replies
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
VOICE = os.environ.get("ENGINEER_VOICE", "")                          # macOS voice name; "" = system default
SPEECH_RATE = int(os.environ.get("ENGINEER_RATE", "185"))             # words per minute for the voice
DRIVER_NAME = os.environ.get("DRIVER_NAME", "John")
PORT = int(os.environ.get("F1_UDP_PORT", str(DEFAULT_PORT)))
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", str(Path(__file__).resolve().parent / "sessions")))
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8000"))                 # http://localhost:8000
DASHBOARD_FILE = Path(__file__).resolve().parent / "dashboard.html"
CORNER_NAMES_FILE = Path(__file__).resolve().parent / "corner_names.json"

# Built-in turn lists: track -> [(position as a fraction of the lap, label), ...].
#
# The game does NOT broadcast turn numbers, so these are the official turn numbers and corner names for
# each circuit placed at their approximate distance around the lap. The names and numbers are right; the
# positions are estimates, so a whole track can land a turn out of step. The dashboard has a shift control
# for that, and any corner can be renamed — your edits go to corner_names.json and always win.
TRACK_TURNS: dict[str, list] = {
    "Melbourne": [(.08, "T1"), (.10, "T2"), (.19, "T3"), (.22, "T4"), (.28, "T5"), (.35, "T6"), (.44, "T7"),
                  (.49, "T8"), (.58, "T9"), (.66, "T10"), (.73, "T11"), (.83, "T12"), (.90, "T13"), (.95, "T14")],
    "Shanghai": [(.11, "T1"), (.13, "T2"), (.16, "T3"), (.20, "T4"), (.25, "T5"), (.30, "T6"), (.33, "T7"),
                 (.36, "T8"), (.40, "T9"), (.45, "T10"), (.52, "T11"), (.56, "T12"), (.62, "T13"), (.80, "T14"),
                 (.85, "T15"), (.93, "T16")],
    "Bahrain": [(.10, "T1"), (.15, "T2"), (.18, "T3"), (.24, "T4"), (.29, "T5"), (.33, "T6"), (.38, "T7"),
                (.44, "T8"), (.48, "T9"), (.55, "T10"), (.62, "T11"), (.70, "T12"), (.79, "T13"), (.87, "T14"),
                (.93, "T15")],
    "Barcelona": [(.12, "T1"), (.15, "T2"), (.22, "T3"), (.30, "T4"), (.36, "T5"), (.41, "T6"), (.46, "T7"),
                  (.50, "T8"), (.56, "T9"), (.62, "T10"), (.68, "T11"), (.75, "T12"), (.83, "T13"), (.87, "T14"),
                  (.93, "T15"), (.96, "T16")],
    "Monaco": [(.09, "T1 Sainte Devote"), (.19, "T2 Beau Rivage"), (.24, "T3 Massenet"), (.28, "T4 Casino"),
               (.34, "T5 Mirabeau"), (.38, "T6 Hairpin"), (.43, "T7 Mirabeau Bas"), (.47, "T8 Portier"),
               (.55, "T9 Tunnel"), (.66, "T10 Nouvelle Chicane"), (.68, "T11"), (.73, "T12 Tabac"),
               (.78, "T13 Swimming Pool"), (.80, "T14"), (.84, "T15"), (.86, "T16"), (.91, "T17 Rascasse"),
               (.94, "T18"), (.96, "T19 Anthony Noghes")],
    "Montreal": [(.05, "T1"), (.07, "T2"), (.14, "T3"), (.17, "T4"), (.25, "T5"), (.30, "T6"), (.35, "T7"),
                 (.40, "T8"), (.47, "T9"), (.55, "T10"), (.60, "T11"), (.70, "T12"), (.90, "T13"),
                 (.95, "T14 Wall of Champions")],
    "Silverstone": [(.05, "T1 Abbey"), (.08, "T2 Farm"), (.13, "T3 Village"), (.16, "T4 The Loop"),
                    (.19, "T5 Aintree"), (.28, "T6 Brooklands"), (.32, "T7 Luffield"), (.37, "T8 Woodcote"),
                    (.44, "T9 Copse"), (.51, "T10 Maggotts"), (.54, "T11 Becketts"), (.56, "T12"), (.58, "T13"),
                    (.60, "T14 Chapel"), (.71, "T15 Stowe"), (.80, "T16 Vale"), (.83, "T17 Club"), (.85, "T18")],
    "Hungaroring": [(.10, "T1"), (.19, "T2"), (.24, "T3"), (.31, "T4"), (.38, "T5"), (.44, "T6"), (.49, "T7"),
                    (.53, "T8"), (.58, "T9"), (.63, "T10"), (.70, "T11"), (.79, "T12"), (.87, "T13"), (.94, "T14")],
    "Spa": [(.03, "T1 La Source"), (.09, "T2 Eau Rouge"), (.10, "T3 Raidillon"), (.20, "T4"),
            (.28, "T5 Les Combes"), (.29, "T6"), (.31, "T7 Malmedy"), (.36, "T8 Rivage"), (.39, "T9"),
            (.44, "T10"), (.50, "T11 Pouhon"), (.57, "T12 Fagnes"), (.59, "T13"), (.63, "T14 Campus"),
            (.66, "T15 Stavelot"), (.72, "T16"), (.83, "T17 Blanchimont"), (.94, "T18 Bus Stop"), (.96, "T19")],
    "Monza": [(.11, "T1 Rettifilo"), (.12, "T2"), (.20, "T3 Curva Grande"), (.29, "T4 Roggia"), (.30, "T5"),
              (.37, "T6 Lesmo 1"), (.43, "T7 Lesmo 2"), (.61, "T8 Ascari"), (.63, "T9"), (.64, "T10"),
              (.85, "T11 Parabolica")],
    "Singapore": [(.06, "T1"), (.08, "T2"), (.10, "T3"), (.15, "T4"), (.20, "T5"), (.24, "T6"), (.30, "T7"),
                  (.34, "T8"), (.38, "T9"), (.42, "T10"), (.46, "T11"), (.51, "T12"), (.56, "T13"), (.62, "T14"),
                  (.68, "T15"), (.79, "T16"), (.86, "T17"), (.90, "T18"), (.95, "T19")],
    # Suzuka positions are measured from real telemetry rather than estimated.
    "Suzuka": [(.142, "T1"), (.152, "T2"), (.208, "T3"), (.222, "T4"), (.245, "T5"), (.258, "T6"),
               (.276, "T7"), (.399, "T8 Degner 1"), (.429, "T9 Degner 2"), (.455, "T10"),
               (.501, "T11 Hairpin"), (.550, "T12"), (.657, "T13 Spoon"), (.699, "T14 Spoon exit"),
               (.878, "T15 130R"), (.940, "T16 Casio"), (.948, "T17"), (.965, "T18")],
    "Abu Dhabi": [(.07, "T1"), (.11, "T2"), (.16, "T3"), (.21, "T4"), (.36, "T5"), (.40, "T6"), (.44, "T7"),
                  (.48, "T8"), (.55, "T9"), (.60, "T10"), (.65, "T11"), (.70, "T12"), (.75, "T13"), (.83, "T14"),
                  (.89, "T15"), (.95, "T16")],
    "Austin": [(.08, "T1"), (.13, "T2"), (.15, "T3"), (.17, "T4"), (.19, "T5"), (.21, "T6"), (.24, "T7"),
               (.27, "T8"), (.30, "T9"), (.34, "T10"), (.40, "T11"), (.52, "T12"), (.56, "T13"), (.60, "T14"),
               (.68, "T15"), (.75, "T16"), (.78, "T17"), (.81, "T18"), (.88, "T19"), (.94, "T20")],
    "Interlagos": [(.09, "T1 Senna S"), (.11, "T2"), (.18, "T3"), (.26, "T4 Descida do Lago"), (.33, "T5"),
                   (.40, "T6 Ferradura"), (.45, "T7"), (.50, "T8 Laranja"), (.55, "T9 Pinheirinho"),
                   (.61, "T10 Bico de Pato"), (.67, "T11 Mergulho"), (.74, "T12 Juncao"), (.82, "T13"),
                   (.88, "T14"), (.94, "T15 Arquibancadas")],
    "Austria": [(.13, "T1"), (.25, "T2"), (.40, "T3"), (.52, "T4"), (.60, "T5"), (.68, "T6"), (.76, "T7"),
                (.83, "T8"), (.90, "T9"), (.96, "T10")],
    "Mexico": [(.19, "T1"), (.21, "T2"), (.23, "T3"), (.29, "T4"), (.34, "T5"), (.39, "T6"), (.44, "T7"),
               (.48, "T8"), (.51, "T9"), (.55, "T10"), (.58, "T11"), (.63, "T12"), (.68, "T13"), (.76, "T14"),
               (.83, "T15"), (.90, "T16 Peraltada"), (.95, "T17")],
    "Baku": [(.12, "T1"), (.15, "T2"), (.20, "T3"), (.24, "T4"), (.27, "T5"), (.31, "T6"), (.35, "T7"),
             (.40, "T8 Castle"), (.43, "T9"), (.46, "T10"), (.49, "T11"), (.53, "T12"), (.57, "T13"),
             (.60, "T14"), (.63, "T15 Filarmonica"), (.67, "T16"), (.70, "T17"), (.73, "T18"), (.76, "T19"),
             (.82, "T20")],
    "Zandvoort": [(.05, "T1 Tarzan"), (.11, "T2 Gerlach"), (.17, "T3 Hugenholtz"), (.25, "T4 Hunserug"),
                  (.30, "T5"), (.35, "T6"), (.42, "T7 Scheivlak"), (.50, "T8"), (.55, "T9"),
                  (.62, "T10 Mastersbocht"), (.72, "T11"), (.79, "T12"), (.86, "T13 Kumho"),
                  (.94, "T14 Arie Luyendyk")],
    "Imola": [(.11, "T1 Tamburello"), (.13, "T2"), (.22, "T3 Villeneuve"), (.28, "T4 Tosa"), (.30, "T5"),
              (.40, "T6 Piratella"), (.47, "T7"), (.51, "T8 Acque Minerali"), (.53, "T9"), (.60, "T10"),
              (.63, "T11"), (.66, "T12 Variante Alta"), (.68, "T13"), (.79, "T14 Rivazza"), (.82, "T15"),
              (.86, "T16"), (.90, "T17"), (.94, "T18"), (.97, "T19")],
    "Jeddah": [(.05, "T1"), (.07, "T2"), (.09, "T3"), (.13, "T4"), (.15, "T5"), (.18, "T6"), (.20, "T7"),
               (.23, "T8"), (.26, "T9"), (.29, "T10"), (.32, "T11"), (.35, "T12"), (.40, "T13"), (.44, "T14"),
               (.47, "T15"), (.50, "T16"), (.53, "T17"), (.57, "T18"), (.60, "T19"), (.63, "T20"), (.67, "T21"),
               (.72, "T22"), (.77, "T23"), (.81, "T24"), (.85, "T25"), (.90, "T26"), (.95, "T27")],
    "Miami": [(.08, "T1"), (.10, "T2"), (.13, "T3"), (.18, "T4"), (.22, "T5"), (.26, "T6"), (.31, "T7"),
              (.35, "T8"), (.38, "T9"), (.41, "T10"), (.46, "T11"), (.51, "T12"), (.55, "T13"), (.62, "T14"),
              (.70, "T15"), (.79, "T16"), (.85, "T17"), (.90, "T18"), (.95, "T19")],
    "Las Vegas": [(.05, "T1"), (.07, "T2"), (.11, "T3"), (.15, "T4"), (.20, "T5"), (.24, "T6"), (.28, "T7"),
                  (.32, "T8"), (.37, "T9"), (.41, "T10"), (.46, "T11"), (.55, "T12"), (.62, "T13"), (.80, "T14"),
                  (.88, "T15"), (.93, "T16"), (.96, "T17")],
    "Qatar": [(.09, "T1"), (.14, "T2"), (.19, "T3"), (.24, "T4"), (.29, "T5"), (.35, "T6"), (.40, "T7"),
              (.46, "T8"), (.51, "T9"), (.57, "T10"), (.63, "T11"), (.69, "T12"), (.75, "T13"), (.82, "T14"),
              (.89, "T15"), (.95, "T16")],
}
DEFAULT_CORNER_NAMES: dict[str, list] = {}
SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 20

# Automatic callouts (rule-based, instant, no API call). Set any to False to silence it.
CALLOUTS = {
    "lap_time": True,        # every completed lap: time, delta to your best, which sector you lost it in
    "safety_car": True,      # safety car / VSC deployed, returning, race resumed
    "flags": True,           # blue and yellow flags
    "penalties": True,       # penalties and warnings for you
    "pit_window": True,      # race only: when the game's ideal pit lap arrives
    "fuel": True,            # race only: fuel going marginal / negative
    "tyre_wear": True,       # 60% and 80% wear on the worst tyre
    "damage": True,          # after a collision involving you, a damage report
    "position": True,        # gained or lost a place (debounced so it doesn't chatter at the start)
    "session": True,         # session start, lights out, chequered flag
}

WHISPER_PROMPT = ("Radio to the race engineer. Tyres, tyre wear, undercut, overcut, box this lap, pit window, "
                  "safety car, ERS, fuel, gap ahead, gap behind, sector, Zandvoort, Silverstone, Monza, "
                  "Overtake mode, active aero, brake bias, front wing.")

SYSTEM_PROMPT = f"""You are the race engineer on the pit wall for {DRIVER_NAME}, who is driving in the EA SPORTS F1 game (F1 25 / F1 26 season) on Xbox. Every question comes with a LIVE TELEMETRY block straight from the game — that is your only source of numbers.

How you talk: like a real race engineer over team radio. Calm, brief, specific. One to three short sentences. Plain spoken English — no markdown, no bullet points, no headers, no emojis. Round sensibly: lap deltas in tenths, gaps to one decimal, percentages whole. Say times the way a radio would ("one thirteen four", "three tenths").

How you think: read the telemetry first, then answer the actual question. When asked for a call (pit now? push? save?), give a clear recommendation and the one reason behind it. When asked where time is being lost, compare his recent lap sectors to his best sectors and the ideal lap, and name the sector. In 2026-regulation cars there is no DRS: active aero straight mode and Overtake mode are the equivalents. If the snapshot lacks the data for a question, say so in a few words instead of guessing. Never read the whole snapshot back."""


def spoken_delta(ms: float) -> str:
    """Radio-style time delta: 'a tenth', 'three tenths', '1.4 seconds'."""
    tenths = int(round(abs(ms) / 100))
    if tenths == 0:
        return "under a tenth"
    if tenths == 1:
        return "a tenth"
    if tenths < 10:
        return f"{tenths} tenths"
    return f"{abs(ms) / 1000:.1f} seconds"


# ----------------------------------------------------------------------------- voice output

class Speaker:
    """Speaks text through macOS `say`, one message at a time, from a background thread."""

    def __init__(self, voice: str = "", rate: int = 185, enabled: bool = True):
        self.voice = voice
        self.rate = rate
        self.enabled = enabled
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="speaker").start()

    def say(self, text: str, tag: str = "ENGINEER") -> None:
        print(f"\n🎧 {tag}: {text}\n", flush=True)
        self.q.put(text)

    def wait(self) -> None:
        self.q.join()

    def _worker(self) -> None:
        while True:
            text = self.q.get()
            try:
                if self.enabled:
                    cmd = ["say", "-r", str(self.rate)]
                    if self.voice:
                        cmd += ["-v", self.voice]
                    cmd.append(text)
                    subprocess.run(cmd, check=False)
            except FileNotFoundError:
                self.enabled = False
                print("(the 'say' command isn't available — voice output off, text only)")
            finally:
                self.q.task_done()


# ----------------------------------------------------------------------------- microphone + Whisper

class Recorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        import sounddevice as sd  # imported here so --test never needs audio
        self.sd = sd
        self.sample_rate = sample_rate
        self.frames: list = []
        self.stream = None

    def start(self) -> None:
        self.frames = []
        self.stream = self.sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                          callback=self._callback)
        self.stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self.frames.append(indata.copy())

    def stop(self):
        import numpy as np
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.frames).flatten()


class Transcriber:
    def __init__(self, model: str = WHISPER_MODEL):
        import mlx_whisper
        import numpy as np
        self.mlx_whisper = mlx_whisper
        self.model = model
        print(f"Loading speech model {model} (first time downloads it, ~1.5 GB) ...", flush=True)
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))   # warm-up so the first radio call is quick
        print("Speech model ready.", flush=True)

    def transcribe(self, audio) -> str:  # noqa: ANN001
        result = self.mlx_whisper.transcribe(audio, path_or_hf_repo=self.model, language="en",
                                             initial_prompt=WHISPER_PROMPT, fp16=True)
        return result["text"].strip()


# ----------------------------------------------------------------------------- Claude

class Engineer:
    def __init__(self, state: RaceState, model: str = MODEL):
        from anthropic import Anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("No ANTHROPIC_API_KEY found. Put it in the .env file next to engineer.py:\n"
                             "    ANTHROPIC_API_KEY=sk-ant-...")
        self.client = Anthropic()
        self.model = model
        self.state = state
        self.history: list[dict] = []   # past turns, questions only (no old snapshots — saves tokens)

    def ask(self, question: str) -> str:
        snapshot = self.state.snapshot_text()
        messages = list(self.history[-8:])
        messages.append({"role": "user", "content": f"{question}\n\n[LIVE TELEMETRY]\n{snapshot}"})
        t0 = time.time()
        response = self.client.messages.create(model=self.model, max_tokens=220,
                                               system=SYSTEM_PROMPT, messages=messages)
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        print(f"   (Claude replied in {time.time() - t0:.1f}s)")
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        return text


# ----------------------------------------------------------------------------- automatic callouts

class Callouts(threading.Thread):
    """Watches the telemetry and speaks the time-critical stuff instantly — no LLM in the loop."""

    def __init__(self, state: RaceState, speaker: Speaker):
        super().__init__(daemon=True, name="callouts")
        self.state = state
        self.speaker = speaker
        self._stop = threading.Event()
        self.session_uid: int | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.last_event_time = time.time()
        self.last_lap_num: int | None = None
        self.pending_lap: tuple[float, int] | None = None   # (announce_at, completed lap number)
        self.pending_damage: float | None = None
        self.last_flag = 0
        self.flag_cooldown = 0.0
        self.pit_window_announced_lap: int | None = None
        self.fuel_state = "ok"
        self.fuel_next_ok = 0.0
        self.wear_announced = 0
        self.last_tyre_age = 0
        self.last_position: int | None = None
        self.position_announced: int | None = None
        self.position_since = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # never let a callout bug kill the radio
                print(f"(callout error: {e})")
            time.sleep(0.2)

    def _tick(self) -> None:
        st = self.state
        with st.lock:
            if st.session_uid != self.session_uid:   # new session → forget everything
                self._reset_state()
                self.session_uid = st.session_uid
                self.last_event_time = time.time() - 1
            lap = st.player(st.lap)
            status = st.player(st.status)
            dmg = st.player(st.damage)
            session = st.session
            race = st.is_race()
            pi = st.player_idx

        now = time.time()

        # --- events from the game
        for e in st.pop_events(self.last_event_time):
            self.last_event_time = max(self.last_event_time, e["time"])
            self._handle_event(e, pi, lap)

        if not lap:
            return

        # --- lap completed
        if self.last_lap_num is not None and lap["currentLapNum"] > self.last_lap_num and lap["lastLapTimeInMS"] > 0:
            self.pending_lap = (now + 1.5, lap["currentLapNum"] - 1)   # wait for the history packet
        self.last_lap_num = lap["currentLapNum"]
        if self.pending_lap and now >= self.pending_lap[0]:
            _, completed = self.pending_lap
            self.pending_lap = None
            if CALLOUTS["lap_time"] and lap["driverStatus"] in (1, 4):
                self._announce_lap(completed, lap, pi)

        # --- flags
        if CALLOUTS["flags"] and status:
            flag = status["vehicleFiaFlags"]
            if flag != self.last_flag and now > self.flag_cooldown:
                if flag == 2:
                    self.speaker.say("Blue flag, blue flag. Let him through when it's safe.", "RADIO")
                elif flag == 3:
                    self.speaker.say("Yellow flag in this sector, no overtaking.", "RADIO")
                self.flag_cooldown = now + 4
            self.last_flag = flag

        # --- pit window (race)
        if CALLOUTS["pit_window"] and race and session and session["pitStopWindowIdealLap"]:
            ideal = session["pitStopWindowIdealLap"]
            if lap["currentLapNum"] == ideal and self.pit_window_announced_lap != ideal and lap["pitStatus"] == 0:
                self.pit_window_announced_lap = ideal
                self.speaker.say(f"Pit window is open. Ideal lap is now, latest is lap {session['pitStopWindowLatestLap']}. "
                                 f"You'd rejoin around P{session['pitStopRejoinPosition']}.", "RADIO")

        # --- fuel (race)
        if CALLOUTS["fuel"] and race and status and status["fuelCapacity"] > 0 and now > self.fuel_next_ok:
            rem = status["fuelRemainingLaps"]
            if rem < 0 and self.fuel_state != "negative":
                self.fuel_state = "negative"
                self.speaker.say(f"Fuel is negative, {abs(rem):.1f} laps short. Lean mix, lift and coast into the slow corners.", "RADIO")
                self.fuel_next_ok = now + 60
            elif 0 <= rem < 0.5 and self.fuel_state == "ok":
                self.fuel_state = "marginal"
                self.speaker.say(f"Fuel is marginal, plus {rem:.1f} laps. Bit of lift and coast will cover it.", "RADIO")
                self.fuel_next_ok = now + 60
            elif rem >= 1.0 and self.fuel_state != "ok":
                self.fuel_state = "ok"
                self.speaker.say(f"Fuel is fine again, plus {rem:.1f} laps. You can push.", "RADIO")
                self.fuel_next_ok = now + 60

        # --- tyre wear
        if CALLOUTS["tyre_wear"] and status and dmg:
            if status["tyresAgeLaps"] < self.last_tyre_age:      # new tyres fitted
                self.wear_announced = 0
            self.last_tyre_age = status["tyresAgeLaps"]
            worst = max(dmg["tyresWear"])
            for level in (60, 80):
                if worst >= level and self.wear_announced < level:
                    self.wear_announced = level
                    which = ("rear left", "rear right", "front left", "front right")[list(dmg["tyresWear"]).index(worst)]
                    self.speaker.say(f"Tyre wear {level} percent on the {which}. "
                                     f"{'Start thinking about the box.' if level == 60 else 'They are going away, box soon.'}", "RADIO")

        # --- damage report after contact
        if self.pending_damage and now >= self.pending_damage:
            self.pending_damage = None
            if dmg:
                bits = []
                if dmg["frontLeftWingDamage"] or dmg["frontRightWingDamage"]:
                    bits.append(f"front wing left {dmg['frontLeftWingDamage']}, right {dmg['frontRightWingDamage']} percent")
                if dmg["rearWingDamage"]:
                    bits.append(f"rear wing {dmg['rearWingDamage']} percent")
                if dmg["floorDamage"]:
                    bits.append(f"floor {dmg['floorDamage']} percent")
                if dmg["sidepodDamage"]:
                    bits.append(f"sidepod {dmg['sidepodDamage']} percent")
                self.speaker.say(("Damage check: " + ", ".join(bits) + ".") if bits else "Contact, but no damage showing. Carry on.", "RADIO")

        # --- position changes (debounced 4 s, ignored in the pits)
        if CALLOUTS["position"] and race:
            pos = lap["carPosition"]
            if self.position_announced is None:
                self.position_announced = pos
            if pos != self.last_position:
                self.last_position = pos
                self.position_since = now
            elif now - self.position_since > 4 and pos != self.position_announced and lap["pitStatus"] == 0:
                gained = pos < self.position_announced
                self.position_announced = pos
                self.speaker.say(f"{'Nice, ' if gained else 'Lost a place, '}you're P{pos}.", "RADIO")

    def _announce_lap(self, completed: int, lap: dict, pi: int | None) -> None:
        st = self.state
        with st.lock:
            last = lap["lastLapTimeInMS"]
            in_pits = lap["pitStatus"] != 0
            prev_best = st.best_lap_ms(pi, exclude_lap=completed)
            sectors = st.lap_sectors_ms(pi, completed)
            best_sectors = st.best_sectors_ms(pi)
            valid = st.lap_valid(pi, completed)
            tt = st.time_trial
        if in_pits or not last:
            return
        if prev_best and last > prev_best * 1.3:
            return  # out lap, in lap, or a big off — not worth reading out
        msg = f"Last lap {fmt_lap_spoken(last)}."
        if valid is False:
            msg += " That one was invalidated."
        reference, ref_name = None, ""
        if tt and tt["personalBest"]["valid"] and tt["personalBest"]["lapTimeInMS"]:
            reference, ref_name = tt["personalBest"]["lapTimeInMS"], "your personal best"
        elif prev_best:
            reference, ref_name = prev_best, "your best"
        if reference:
            delta = last - reference
            if abs(delta) < 50:
                msg += f" Right on {ref_name}."
            elif delta < 0:
                msg += f" New best, {spoken_delta(-delta)} up on {ref_name}."
            else:
                msg += f" {spoken_delta(delta).capitalize()} off {ref_name}."
                if sectors and all(best_sectors):
                    losses = [sectors[i] - best_sectors[i] for i in range(3)]  # type: ignore[operator]
                    worst = max(range(3), key=lambda i: losses[i])
                    if losses[worst] > 100:
                        msg += f" Most of it in sector {worst + 1}, {spoken_delta(losses[worst])}."
        else:
            msg += " First lap on the board."
        self.speaker.say(msg, "RADIO")

    def _handle_event(self, e: dict, pi: int | None, lap: dict | None) -> None:
        c = e["code"]
        if c == "SSTA" and CALLOUTS["session"]:
            with self.state.lock:
                s = self.state.session
            where = f"{SESSION_TYPES.get(s['sessionType'], 'session')} at {TRACKS.get(s['trackId'], 'the track')}" if s else "the session"
            self.speaker.say(f"Radio check, {DRIVER_NAME}. Telemetry's up for {where}.", "RADIO")
        elif c == "LGOT" and CALLOUTS["session"]:
            self.speaker.say("Lights out. Go, go, go.", "RADIO")
        elif c == "CHQF" and CALLOUTS["session"]:
            pos = f" P{lap['carPosition']}." if lap else ""
            self.speaker.say(f"Chequered flag.{pos} Good work out there.", "RADIO")
        elif c == "SCAR" and CALLOUTS["safety_car"]:
            kind = {1: "Safety car", 2: "Virtual safety car", 3: "Safety car"}.get(e["safetyCarType"], "Safety car")
            what = e["eventType"]
            if what == 0:
                self.speaker.say(f"{kind} deployed, {kind.lower()} deployed. Keep the delta positive.", "RADIO")
            elif what == 1:
                self.speaker.say(f"{kind} in this lap. Get the tyres and brakes up to temperature.", "RADIO")
            elif what == 3:
                self.speaker.say("Green flag, racing again.", "RADIO")
        elif c == "PENA" and CALLOUTS["penalties"] and e["vehicleIdx"] == pi:
            ptype = PENALTY_TYPES.get(e["penaltyType"], "penalty")
            why = INFRINGEMENTS.get(e["infringementType"], "")
            secs = f", {e['timeSeconds']} seconds" if e["timeSeconds"] and e["penaltyType"] == 4 else ""
            self.speaker.say(f"Race control: {ptype}{secs}{', ' + why if why else ''}.", "RADIO")
        elif c == "COLL" and CALLOUTS["damage"] and pi in (e["vehicle1Idx"], e["vehicle2Idx"]):
            self.pending_damage = time.time() + 2.5
        elif c == "DRSE" and CALLOUTS["session"]:
            self.speaker.say("DRS enabled.", "RADIO")
        elif c == "RDFL" and CALLOUTS["session"]:
            self.speaker.say("Red flag, red flag. Slow down and come into the pits.", "RADIO")


# ----------------------------------------------------------------------------- session recording

class SessionLogger:
    """Records the session to sessions/<date>_<track>_<type>/ : laps.csv, trace.csv, session.json.

    Hooked into RaceState.on_packet, so it runs inside the state lock — it must be quick and must
    not take the lock itself. Flushes to disk every 20 s and on quit.
    """

    TRACE_COLUMNS = ["session_time_s", "lap", "sector", "distance_m", "lap_time_ms", "speed_kph", "speed_mph",
                     "throttle", "brake", "steer", "gear", "rpm", "aero_or_drs", "overtake", "ers_mode", "ers_pct", "fuel_kg",
                     "tyre_surface_fl", "tyre_surface_fr", "tyre_surface_rl", "tyre_surface_rr",
                     "tyre_inner_fl", "tyre_inner_fr", "tyre_inner_rl", "tyre_inner_rr",
                     "tyre_pressure_fl", "tyre_pressure_fr", "tyre_pressure_rl", "tyre_pressure_rr",
                     "brake_temp_fl", "brake_temp_fr", "brake_temp_rl", "brake_temp_rr",
                     "world_x", "world_y", "world_z"]

    def __init__(self, state: RaceState, root: Path = SESSIONS_DIR):
        self.state = state
        self.opponents = OpponentTraces(state)
        self.coaching = CoachingService()
        self.setup_baselines = SetupBaselineService(root.parent / 'setup-baselines')
        self.setup_history = []
        self.incident_timeline = []
        self.lap_flags = {}
        self.session_ended = False
        self.coaching_lap_count = 0
        self.root = root
        self.folder: Path | None = None
        self.session_uid: int | None = None
        self.trace: list[list] = []
        self.by_lap: dict[int, list[list]] = {}
        self.trace_flushed = 0
        self.lap_snapshots: dict[int, dict] = {}
        self.last_lap_num: int | None = None
        self.last_flush = time.time()
        self.io_lock = threading.Lock()
        self.write_queue = queue.Queue()
        threading.Thread(target=self._writer_loop, daemon=True, name="recording-writer").start()
        state.on_packet = self.on_packet
        state.on_session_end = self.on_session_end
        threading.Thread(target=self._flush_loop, daemon=True, name="logger-flush").start()

    # called under state.lock, just before the game's next session wipes the state
    def on_session_end(self) -> None:
        self.session_ended = True
        payload = self._collect_locked()
        if payload:
            self.write_queue.put((payload, None, None))
        self._reset_buffers()

    # called under state.lock
    def on_packet(self, pid: int) -> None:
        self.opponents.ingest(pid)
        st = self.state
        if st.session_uid != self.session_uid:
            self._reset_buffers()
            self.session_uid = st.session_uid
        if pid == PKT_HISTORY:
            history = st.player_history()
            count = sum(1 for l in history['laps'] if l['lapTimeInMS'] > 0) if history else 0
            if count > self.coaching_lap_count:
                self.coaching_lap_count = count
                threading.Thread(target=self.flush, daemon=True, name='lap-save').start()
        lap = st.player(st.lap)
        if pid == PKT_SETUPS:
            setup = st.player(st.setup)
            if setup and (not self.setup_history or setup != self.setup_history[-1]['setup']):
                self.setup_history.append({'revision': len(self.setup_history)+1, 'session_time_s': st.session_time,
                                           'lap': lap.get('currentLapNum') if lap else None, 'setup': dict(setup)})
        if pid == PKT_EVENT and getattr(st, 'latest_event', None):
            event = st.latest_event
            code = event.get('code')
            if code != 'BUTN':
                self.incident_timeline.append(dict(event, lap=lap.get('currentLapNum') if lap else None,
                                                  description=st.describe_event(event)))
            if code == 'SEND': self.session_ended = True
            if lap and (code == 'FLBK' or code == 'COLL' and st.player_idx in (event.get('vehicle1Idx'),event.get('vehicle2Idx'))):
                self.lap_flags.setdefault(lap['currentLapNum'], {})['interrupted'] = True
        if not lap:
            return
        if pid == PKT_LAP:
            if self.last_lap_num is not None and lap["currentLapNum"] > self.last_lap_num and lap["lastLapTimeInMS"] > 0:
                self._snapshot_lap(lap["currentLapNum"] - 1, lap)
            self.last_lap_num = lap["currentLapNum"]
            flags = self.lap_flags.setdefault(lap['currentLapNum'], {'pit_lap':False,'interrupted':False})
            flags.setdefault('pit_lap',False)
            flags.setdefault('interrupted',False)
            flags['pit_lap'] |= bool(lap['pitStatus']) or lap['driverStatus'] in (0,2,3)
            flags['interrupted'] |= bool(st.session and st.session.get('safetyCarStatus'))
            cs = st.player(st.status)
            flags['interrupted'] |= bool(cs and cs.get('vehicleFiaFlags',-1) in (2,3))
            revision = self.setup_history[-1]['revision'] if self.setup_history else None
            if 'setup_revision' in flags and flags['setup_revision'] != revision: flags['interrupted'] = True
            flags.setdefault('setup_revision', revision)
        elif pid == PKT_TELEMETRY:
            tel = st.player(st.telemetry)
            if not tel or lap["driverStatus"] == 0 or (st.session and st.session["gamePaused"]):
                return   # in the garage or paused: nothing worth recording
            cs = st.player(st.status)
            t2 = st.player(st.telemetry2)
            if t2 and t2["regulations2026"]:
                aero = t2["activeAeroMode"]
                ovt = t2["overtakeActive"]
            else:
                aero = tel["drs"]
                ovt = 0
            p = tel["tyresPressure"]
            row = [
                round(st.session_time, 3), lap["currentLapNum"], lap["sector"] + 1, round(lap["lapDistance"], 1),
                lap["currentLapTimeInMS"], tel["speed"], round(tel["speed"] * 0.621371, 1),
                round(tel["throttle"], 3), round(tel["brake"], 3), round(tel["steer"], 3), tel["gear"], tel["engineRPM"],
                aero, ovt, cs["ersDeployMode"] if cs else 0,
                round(cs["ersStoreEnergy"] / 4_000_000 * 100, 1) if cs else 0, round(cs["fuelInTank"], 2) if cs else 0,
                *tel["tyresSurfaceTemperature"][2:4], *tel["tyresSurfaceTemperature"][0:2],
                *tel["tyresInnerTemperature"][2:4], *tel["tyresInnerTemperature"][0:2],
                round(p[2], 2), round(p[3], 2), round(p[0], 2), round(p[1], 2),
                *tel["brakesTemperature"][2:4], *tel["brakesTemperature"][0:2],
                round(m["x"], 1) if (m := st.motion) else "", round(m["y"], 1) if m else "",
                round(m["z"], 1) if m else "",
            ]
            self.trace.append(row)
            self.by_lap.setdefault(lap["currentLapNum"], []).append(row)
            self._snapshot_lap(lap["currentLapNum"], lap)

    def _reset_buffers(self) -> None:
        self.session_uid = None
        self.folder = None
        self.trace = []
        self.by_lap = {}
        self.trace_flushed = 0
        self.lap_snapshots = {}
        self.last_lap_num = None
        self.setup_history = []
        self.incident_timeline = []
        self.lap_flags = {}
        self.session_ended = False
        self.coaching_lap_count = 0

    def _snapshot_lap(self, lap_num: int, lap: dict) -> None:
        # Keep the final on-track sample for that lap; next-lap packets may already carry new tyres.
        if lap_num < lap['currentLapNum'] and lap_num in self.lap_snapshots:
            return
        st = self.state
        cs = st.player(st.status)
        dmg = st.player(st.damage)
        snap = {"lap": lap_num, "time_ms": lap["lastLapTimeInMS"], "time": fmt_lap(lap["lastLapTimeInMS"]),
                "pit_stops": lap["numPitStops"], "position": lap["carPosition"]}
        if cs:
            snap.update({"compound": VISUAL_COMPOUND.get(cs["visualTyreCompound"], cs["visualTyreCompound"]),
                         "tyre_age_laps": cs["tyresAgeLaps"], "fuel_kg": round(cs["fuelInTank"], 2),
                         "fuel_laps_remaining": round(cs["fuelRemainingLaps"], 2),
                         "fuel_mix": FUEL_MIX.get(cs["fuelMix"], cs["fuelMix"]),
                         "ers_pct": round(cs["ersStoreEnergy"] / 4_000_000 * 100, 1),
                         "ers_deployed_mj": round(cs["ersDeployedThisLap"] / 1e6, 2),
                         "ers_harvested_mj": round((cs["ersHarvestedThisLapMGUK"] + cs["ersHarvestedThisLapMGUH"]) / 1e6, 2)})
        if dmg:
            w = dmg["tyresWear"]
            snap.update({"wear_fl": round(w[2], 1), "wear_fr": round(w[3], 1), "wear_rl": round(w[0], 1), "wear_rr": round(w[1], 1),
                         "front_wing_damage": max(dmg["frontLeftWingDamage"], dmg["frontRightWingDamage"]), "rear_wing_damage": dmg["rearWingDamage"]})
        if st.session:
            snap['weather'] = st.session['weather']
            snap['track_temp_c'] = st.session['trackTemperature']
            snap['assists_key'] = json.dumps([st.session.get(k) for k in ('tractionControlAssist','antiLockBrakesAssist','gearboxAssist','brakingAssist','steeringAssist')])
        snap.update(self.lap_flags.get(lap_num, {}))
        snap['setup_revision'] = self.lap_flags.get(lap_num, {}).get('setup_revision', self.setup_history[-1]['revision'] if self.setup_history else None)
        self.lap_snapshots[lap_num] = snap

    def _ensure_folder(self) -> Path | None:
        if self.folder:
            return self.folder
        s = self.state.session
        if not s:
            return None
        track = TRACKS.get(s["trackId"], f'track{s["trackId"]}').replace(" ", "")
        stype = SESSION_TYPES.get(s["sessionType"], "session").replace(" ", "")
        base = self.root / f'{datetime.now():%Y-%m-%d_%H-%M}_{track}_{stype}'
        folder, n = base, 2
        while folder.exists():                      # second session in the same minute → suffix
            folder = base.with_name(f"{base.name}_{n}")
            n += 1
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        return self.folder

    def _flush_loop(self) -> None:
        while True:
            time.sleep(5)
            if time.time() - self.last_flush > 20:
                self.flush()

    def _writer_loop(self):
        while True:
            payload, done, errors = self.write_queue.get()
            try:
                self._write(*payload)
            except Exception as error:
                if errors is not None: errors.append(error)
                print(f'(recording write error: {error})', file=sys.stderr)
            finally:
                if done: done.set()
                self.write_queue.task_done()

    def flush(self) -> Path | None:
        """Queue under the state lock so append order matches sample reservation order."""
        done = threading.Event()
        errors = []
        with self.state.lock:
            payload = self._collect_locked()
            if payload: self.write_queue.put((payload, done, errors))
        if not payload: return None
        if not done.wait(10): raise RuntimeError('Recording save timed out')
        if errors: raise errors[0]
        return payload[0]

    def _collect_locked(self):
        folder = self._ensure_folder()
        if not folder:
            return None
        new_rows = self.trace[self.trace_flushed:]
        self.trace_flushed = len(self.trace)
        return folder, new_rows, self._laps_table_locked(), self._session_info_locked(), self.opponents.snapshot(include_rows=True)

    def _write(self, folder: Path, new_rows: list, laps: list[dict], info: dict, opponents: dict) -> None:
        with self.io_lock:
            trace_path = folder / "trace.csv"
            write_header = not trace_path.exists()
            with open(trace_path, "a", newline="") as fh:
                w = csv.writer(fh)
                if write_header:
                    w.writerow(self.TRACE_COLUMNS)
                w.writerows(new_rows)
            if laps:
                with open(folder / "laps.csv.tmp", "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(laps[0].keys()))
                    w.writeheader()
                    w.writerows(laps)
                (folder / "laps.csv.tmp").replace(folder / "laps.csv")
            with open(folder / "session.json.tmp", "w") as fh:
                json.dump(info, fh, indent=1)
            (folder / "session.json.tmp").replace(folder / "session.json")
            path = folder / "opponents.json"
            temporary = folder / "opponents.json.tmp"
            temporary.write_text(json.dumps(opponents, separators=(",", ":")))
            temporary.replace(path)
        self.last_flush = time.time()
        if laps: self.coaching.request(folder)

    def _laps_table_locked(self) -> list[dict]:
        st = self.state
        h = st.player_history()
        rows = []
        if h:
            for n, l in enumerate(h["laps"], start=1):
                if l["lapTimeInMS"] <= 0:
                    continue
                s1 = sector_ms(l["sector1TimeMSPart"], l["sector1TimeMinutes"])
                s2 = sector_ms(l["sector2TimeMSPart"], l["sector2TimeMinutes"])
                s3 = sector_ms(l["sector3TimeMSPart"], l["sector3TimeMinutes"])
                row = {"lap": n, "time": fmt_lap(l["lapTimeInMS"]), "time_ms": l["lapTimeInMS"],
                       "s1": fmt_lap(s1), "s2": fmt_lap(s2), "s3": fmt_lap(s3),
                       "s1_ms": s1, "s2_ms": s2, "s3_ms": s3,
                       "valid": bool(l["lapValidBitFlags"] & 1)}
                row.update({k: v for k, v in self.lap_snapshots.get(n, {}).items() if k not in ("lap", "time_ms", "time")})
                rows.append(row)
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        return [{k: r.get(k, "") for k in keys} for r in rows]

    def _session_info_locked(self) -> dict:
        st = self.state
        s = st.session
        info: dict = {"recorded": datetime.now().isoformat(timespec="seconds"), "game_version": st.game_version,
                      "packets": st.packets_received, "driver": DRIVER_NAME}
        if s:
            info.update({"track": TRACKS.get(s["trackId"], s["trackId"]), "session_type": SESSION_TYPES.get(s["sessionType"], s["sessionType"]),
                         "total_laps": s["totalLaps"], "track_length_m": s["trackLength"],
                         "weather": WEATHER.get(s["weather"], s["weather"]), "track_temp_c": s["trackTemperature"],
                         "air_temp_c": s["airTemperature"], "ai_difficulty": s["aiDifficulty"],
                         "speed_units": st.speed_units(),
                         "temp_units": "F" if s["temperatureUnitsLeadPlayer"] == 1 else "C",
                         "assists": {"traction_control": s["tractionControlAssist"], "abs": s["antiLockBrakesAssist"],
                                     "gearbox": s["gearboxAssist"], "racing_line": s["dynamicRacingLine"],
                                     "braking": s["brakingAssist"], "steering": s["steeringAssist"],
                                     "pit": s["pitAssist"], "ers": s["ERSAssist"]},
                         "sector2_start_m": s["sector2LapDistanceStart"], "sector3_start_m": s["sector3LapDistanceStart"],
                         "regulations_2026": s["formula"] == 13,"formula":s["formula"]})
        setup = st.player(st.setup)
        if setup:
            info["setup"] = setup
        bs = st.best_sectors_ms(st.player_idx)
        info["best_lap"] = fmt_lap(st.best_lap_ms(st.player_idx))
        info["best_sectors"] = [fmt_lap(x) for x in bs]
        info["ideal_lap"] = fmt_lap(sum(bs)) if all(bs) else None  # type: ignore[arg-type]
        if st.time_trial:
            tt = st.time_trial
            info["time_trial"] = {k: {"lap": fmt_lap(v["lapTimeInMS"]), "s1": fmt_lap(v["sector1TimeInMS"]),
                                      "s2": fmt_lap(v["sector2TimeInMS"]), "s3": fmt_lap(v["sector3TimeInMS"]),
                                      "valid": bool(v["valid"])} for k, v in tt.items()}
        if st.tyre_sets:
            info["tyre_sets"] = [{"compound": VISUAL_COMPOUND.get(t["visualTyreCompound"], "?"), "wear": t["wear"],
                                  "fitted": bool(t["fitted"]), "delta_s_per_lap": t["lapDeltaTime"] / 1000}
                                 for t in st.tyre_sets["sets"] if t["available"]]
        info['setup_history'] = list(self.setup_history)
        info['incident_timeline'] = list(self.incident_timeline)
        info['last_session_time_s'] = st.session_time
        info['session_ended'] = self.session_ended
        info["events"] = [st.describe_event(e) for e in st.events if e.get('code') != 'BUTN'][-60:]
        if st.final:
            info["final_classification"] = [
                {"position": r["position"], "driver": st.car_name(i), "you": i == st.player_idx, "best_lap": fmt_lap(r["bestLapTimeInMS"]),
                 "stops": r["numPitStops"], "penalties_s": r["penaltiesTime"]}
                for i, r in enumerate(st.final["rows"]) if r["position"]]
            info["final_classification"].sort(key=lambda r: r["position"])
        return info


# ----------------------------------------------------------------------------- live dashboard server

class DashboardServer:
    """Serves dashboard.html and a small JSON API on localhost so the browser can draw live graphs."""

    def __init__(self, state: RaceState, logger: SessionLogger, port: int = DASHBOARD_PORT):
        self.state = state
        self.logger = logger
        self.port = None
        app = self
        # Playback: the client can ask for another lap's whole trace or just the rows it hasn't seen.

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep the terminal quiet
                pass

            def _json(self, payload, status: int = 200) -> None:
                body = json.dumps(payload, default=_json_default).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                url = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if url.path == '/api/coaching':
                        folder = app.coaching_folder(body.get('session',''))
                        if not folder: raise ValueError('No recorded session available')
                        self._json(app.logger.coaching.update(folder, body.get('symptom'),
                            body.get('start_trial',False), body.get('clear_trial',False),body.get('goal')))
                    elif url.path == '/api/setup-baseline':
                        info,goal=app.setup_context(body.get('session',''))
                        self._json(app.logger.setup_baselines.get(info,goal,refresh=True))
                    elif url.path == "/api/corner-names":
                        self._json(app.save_corner_names(body.get("track", ""), body.get("names", [])))
                    else:
                        self.send_error(404)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    try:
                        self._json({"error": f"{type(e).__name__}: {e}"}, 500)
                    except Exception:
                        pass

            def do_GET(self) -> None:
                url = urlparse(self.path)
                q = parse_qs(url.query)
                try:
                    if url.path in ("/", "/index.html"):
                        if not DASHBOARD_FILE.exists():
                            self.send_error(404, f"dashboard.html is missing from {DASHBOARD_FILE.parent}")
                            return
                        body = DASHBOARD_FILE.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                    elif url.path == '/api/setup-baseline':
                        info,goal=app.setup_context(q.get('session',[''])[0])
                        self._json(app.logger.setup_baselines.get(info,goal))
                    elif url.path == '/api/coaching':
                        folder = app.coaching_folder(q.get('session',[''])[0])
                        self._json(app.logger.coaching.get(folder) if folder else {'next_action':'Start a session and complete a timed lap to begin coaching.', 'laps':[], 'worker_status':'idle'})
                    elif url.path == "/api/live":
                        self._json(app.live_json())
                    elif url.path == "/api/opponents":
                        self._json(app.opponents_json(q.get("session", [""])[0]))
                    elif url.path == "/api/opponent-trace":
                        self._json(app.opponents_json(q.get("session", [""])[0],
                            int(q.get("driver", ["-1"])[0]), int(q.get("lap", ["0"])[0])))
                    elif url.path == "/api/laps":
                        self._json(app.laps_json())
                    elif url.path == "/api/session":
                        self._json(app.session_json())
                    elif url.path == "/api/trace":
                        lap = int(q.get("lap", ["0"])[0])
                        start = int(q.get("from", ["0"])[0])
                        if q.get("session"):
                            self._json(app.saved_trace_json(q["session"][0], lap))
                        else:
                            self._json(app.trace_json(lap, start))
                    elif url.path == "/api/sessions":
                        self._json(app.sessions_json())
                    elif url.path == "/api/saved":
                        self._json(app.saved_json(q.get("id", [""])[0]))
                    elif url.path == "/api/debug":
                        self._json(app.debug_json(q.get("id", [""])[0]))
                    elif url.path == "/api/corner-names":
                        tl = q.get("length", [""])[0]
                        self._json(app.corner_names_json(q.get("track", [""])[0],
                                                         float(tl) if tl else None))
                    else:
                        self.send_error(404)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:  # noqa: BLE001 — report it instead of dying silently
                    import traceback
                    traceback.print_exc()
                    try:
                        self._json({"error": f"{type(e).__name__}: {e}"}, 500)
                    except Exception:
                        pass

        for p in range(port, port + 10):
            try:
                self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", p), Handler)
                self.port = p
                break
            except OSError:
                continue
        if self.port is None:
            raise SystemExit(f"could not open a port for the dashboard ({port}-{port + 9} all busy)")
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard").start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def live_json(self) -> dict:
        st = self.state
        with st.lock:
            s = st.session
            lap = st.player(st.lap)
            tel = st.player(st.telemetry)
            tel2 = st.player(st.telemetry2)
            cs = st.player(st.status)
            dmg = st.player(st.damage)
            pi = st.player_idx
            st._prune_rate()
            out: dict = {
                "connected": st.seconds_since_packet() < 3,
                "packet_format": st.packet_format,
                "packets_per_second": len(st._rate_window),
                "game_version": st.game_version,
                "driver_name": DRIVER_NAME,
                "comparison": self.comparison_json(),
                "trace_rows": len(self.logger.trace),
            }
            if s:
                out["session"] = {
                    "track": TRACKS.get(s["trackId"], f'track {s["trackId"]}'),
                    "type": SESSION_TYPES.get(s["sessionType"], "session"),
                    "is_race": st.is_race(), "is_time_trial": st.is_time_trial(),
                    "total_laps": s["totalLaps"], "time_left_s": s["sessionTimeLeft"],
                    "weather": WEATHER.get(s["weather"], "?"), "track_temp_c": s["trackTemperature"],
                    "air_temp_c": s["airTemperature"], "safety_car": SAFETY_CAR.get(s["safetyCarStatus"], "?"),
                    "speed_units": st.speed_units(), "temp_units": "F" if s["temperatureUnitsLeadPlayer"] == 1 else "C",
                    "ai_difficulty": s["aiDifficulty"], "regs_2026": s["formula"] == 13,
                    "pit_ideal_lap": s["pitStopWindowIdealLap"], "pit_latest_lap": s["pitStopWindowLatestLap"],
                    "pit_rejoin_position": s["pitStopRejoinPosition"], "paused": bool(s["gamePaused"]),
                    "track_length_m": s["trackLength"],
                    "sector2_start_m": s["sector2LapDistanceStart"], "sector3_start_m": s["sector3LapDistanceStart"],
                    "forecast": [{"offset_min": f["timeOffset"], "weather": WEATHER.get(f["weather"], "?"),
                                  "rain_pct": f["rainPercentage"]} for f in s["forecast"]
                                 if f["sessionType"] == s["sessionType"] and f["timeOffset"] > 0][:6],
                }
            if lap:
                bs = st.best_sectors_ms(pi)
                h = st.player_history()
                out["driver"] = {
                    "position": lap["carPosition"], "lap": lap["currentLapNum"], "sector": lap["sector"] + 1,
                    "status": DRIVER_STATUS.get(lap["driverStatus"], "?"), "in_pit": lap["pitStatus"] != 0,
                    "current_lap_ms": lap["currentLapTimeInMS"], "current_lap_invalid": bool(lap["currentLapInvalid"]),
                    "s1_ms": sector_ms(lap["sector1TimeMSPart"], lap["sector1TimeMinutesPart"]),
                    "s2_ms": sector_ms(lap["sector2TimeMSPart"], lap["sector2TimeMinutesPart"]),
                    "last_lap_ms": lap["lastLapTimeInMS"], "best_lap_ms": st.best_lap_ms(pi),
                    "best_lap_num": h["bestLapTimeLapNum"] if h else 0,
                    "best_sectors_ms": list(bs), "ideal_ms": sum(bs) if all(bs) else None,  # type: ignore[arg-type]
                    "lap_distance_m": lap["lapDistance"],
                    "gap_ahead_ms": sector_ms(lap["deltaToCarInFrontMSPart"], lap["deltaToCarInFrontMinutesPart"]),
                    "gap_leader_ms": sector_ms(lap["deltaToRaceLeaderMSPart"], lap["deltaToRaceLeaderMinutesPart"]),
                    "gap_behind_ms": st._gap_behind_ms(lap),
                    "penalties_s": lap["penalties"], "warnings": lap["totalWarnings"],
                    "corner_cutting_warnings": lap["cornerCuttingWarnings"], "pit_stops": lap["numPitStops"],
                    "grid": lap["gridPosition"],
                }
            if cs:
                out["tyres"] = {
                    "compound": VISUAL_COMPOUND.get(cs["visualTyreCompound"], "?"),
                    "actual": ACTUAL_COMPOUND.get(cs["actualTyreCompound"], "?"), "age_laps": cs["tyresAgeLaps"],
                    "wear": [dmg["tyresWear"][2], dmg["tyresWear"][3], dmg["tyresWear"][0], dmg["tyresWear"][1]] if dmg else None,
                    "surface": [tel["tyresSurfaceTemperature"][i] for i in (2, 3, 0, 1)] if tel else None,
                    "inner": [tel["tyresInnerTemperature"][i] for i in (2, 3, 0, 1)] if tel else None,
                    "pressure": [tel["tyresPressure"][i] for i in (2, 3, 0, 1)] if tel else None,
                }
                out["energy"] = {
                    "fuel_kg": cs["fuelInTank"], "fuel_laps": cs["fuelRemainingLaps"],
                    "fuel_mix": FUEL_MIX.get(cs["fuelMix"], "?"), "ers_pct": cs["ersStoreEnergy"] / 4_000_000 * 100,
                    "ers_mode": ERS_MODE.get(cs["ersDeployMode"], "?"), "deployed_mj": cs["ersDeployedThisLap"] / 1e6,
                    "harvested_mj": (cs["ersHarvestedThisLapMGUK"] + cs["ersHarvestedThisLapMGUH"]) / 1e6,
                }
            if tel:
                out["car"] = {
                    "speed": st.speed_conv(tel["speed"]), "gear": tel["gear"], "rpm": tel["engineRPM"],
                    "max_rpm": cs["maxRPM"] if cs else 0, "throttle": tel["throttle"], "brake": tel["brake"],
                    "steer": tel["steer"], "engine_temp_c": tel["engineTemperature"],
                    "brake_temps": [tel["brakesTemperature"][i] for i in (2, 3, 0, 1)],
                    "flag": FLAGS.get(cs["vehicleFiaFlags"], "?") if cs else "?",
                    "brake_bias": cs["frontBrakeBias"] if cs else None,
                    "drs": tel["drs"],
                    "aero_available": bool(tel2["activeAeroAvailable"]) if tel2 else None,
                    "aero_straight": bool(tel2["activeAeroMode"]) if tel2 else None,
                    "overtake_available": bool(tel2["overtakeAvailable"]) if tel2 else None,
                    "overtake_active": bool(tel2["overtakeActive"]) if tel2 else None,
                }
            if dmg:
                out["damage"] = {k: dmg[k] for k in ("frontLeftWingDamage", "frontRightWingDamage", "rearWingDamage",
                                                      "floorDamage", "diffuserDamage", "sidepodDamage", "gearBoxDamage",
                                                      "engineDamage", "drsFault", "ersFault")}
            if s and not st.is_time_trial():
                field = []
                for i, l in enumerate(st.lap):
                    if not l or l["resultStatus"] not in (2, 3) or l["carPosition"] == 0:
                        continue
                    c = st.status[i]
                    p = st.participants[i]
                    field.append({"position": l["carPosition"], "you": i == pi,
                                  "name": (p["name"] if p and p["name"] else f"car {i}"),
                                  "team": TEAMS.get(p["teamId"], "") if p else "",
                                  "last_ms": l["lastLapTimeInMS"], "best_ms": st.best_lap_ms(i),
                                  "gap_leader_ms": sector_ms(l["deltaToRaceLeaderMSPart"], l["deltaToRaceLeaderMinutesPart"]),
                                  "gap_ahead_ms": sector_ms(l["deltaToCarInFrontMSPart"], l["deltaToCarInFrontMinutesPart"]),
                                  "stops": l["numPitStops"], "lap": l["currentLapNum"], "in_pit": l["pitStatus"] != 0,
                                  "compound": VISUAL_COMPOUND.get(c["visualTyreCompound"], "?") if c else "?",
                                  "tyre_age": c["tyresAgeLaps"] if c else None})
                field.sort(key=lambda r: r["position"])
                out["field"] = field
            if st.time_trial:
                out["time_trial"] = {k: {"lap_ms": v["lapTimeInMS"], "s1_ms": v["sector1TimeInMS"], "s2_ms": v["sector2TimeInMS"],
                                         "s3_ms": v["sector3TimeInMS"], "valid": bool(v["valid"])}
                                     for k, v in st.time_trial.items()}
            out["events"] = [{"t": e["sessionTime"], "text": st.describe_event(e)} for e in list(st.events)[-12:]]
            return out

    def setup_context(self,sid=''):
        folder=self.coaching_folder(sid)
        info=load_json(folder/'session.json',{}) if folder else {}
        state=load_json(folder/'coaching-state.json',{}) if folder else {}
        if not sid:
            with self.state.lock:
                session=self.state.session
                if session:
                    info=dict(info,track=TRACKS.get(session['trackId']),formula=session['formula'],regulations_2026=session['formula']==13,
                              weather=WEATHER.get(session['weather']),setup=dict(self.state.player(self.state.setup) or {}),
                              assists={'traction_control':session['tractionControlAssist'],'abs':session['antiLockBrakesAssist']})
        return info,state.get('goal','qualifying')

    def coaching_folder(self, sid=''):
        if sid: return self._saved_dir(sid)
        return self.logger.folder

    def opponents_json(self, sid="", driver_id=None, lap=None) -> dict:
        if not sid:
            with self.state.lock:
                if driver_id is None:
                    return self.logger.opponents.snapshot()
                return self.logger.opponents.trace(driver_id, lap)
        directory = self._saved_dir(sid)
        if directory is None:
            return {"error": "No such saved session."}
        path = directory / "opponents.json"
        if not path.exists():
            return {"error": "This older recording has no opponent lap traces. Record a new practice, qualifying, or race session."}
        data = json.loads(path.read_text())
        if driver_id is not None:
            driver = next((d for d in data['drivers'] if d['id'] == driver_id), {})
            record = next((r for r in driver.get('laps', []) if r['lap'] == lap), None)
            return {"session_id": data['session_id'], "columns": data['columns'], "trace": record}
        for driver in data['drivers']:
            for record in driver['laps']:
                record.pop('rows', None)
        return data

    def comparison_json(self) -> dict:
        """Called under the live snapshot lock; hidden values must never look like zero."""
        st = self.state
        drivers = []
        if st.session and not st.is_time_trial():
            for i, lap in enumerate(st.lap):
                if not lap or lap["resultStatus"] not in (2, 3, 4, 5, 6, 7) or not lap["carPosition"]:
                    continue
                p, tel, status, damage = st.participants[i], st.telemetry[i], st.status[i], st.damage[i]
                own = i == st.player_idx
                access = "own" if own else ("public" if p and (p.get("aiControlled") or p.get("yourTelemetry") == 1)
                                             else "restricted" if p else "unknown")
                shared = access in ("own", "public")
                drivers.append({
                    "id": i, "you": own, "name": st.car_name(i),
                    "ai": bool(p and p.get("aiControlled")), "access": access,
                    "position": lap["carPosition"], "lap": lap["currentLapNum"],
                    "in_pit": bool(lap["pitStatus"]), "result_status": lap["resultStatus"],
                    "last_ms": lap["lastLapTimeInMS"] or None, "best_ms": st.best_lap_ms(i) or None,
                    "sectors_ms": list(st.best_sectors_ms(i)), "stops": lap["numPitStops"],
                    "speed": st.speed_conv(tel["speed"]) if tel else None,
                    "throttle": tel["throttle"] * 100 if tel else None,
                    "brake": tel["brake"] * 100 if tel else None,
                    "gear": tel["gear"] if tel else None,
                    "compound": VISUAL_COMPOUND.get(status["visualTyreCompound"], "?") if status else None,
                    "tyre_age": status["tyresAgeLaps"] if status else None,
                    "fuel_kg": status["fuelInTank"] if status and shared else None,
                    "ers_pct": status["ersStoreEnergy"] / 4_000_000 * 100 if status and shared else None,
                    "wear": [damage["tyresWear"][j] for j in (2, 3, 0, 1)] if damage and shared else None,
                })
        return {"session_id": str(st.session_uid), "drivers": sorted(drivers, key=lambda d: d["position"])}

    def laps_json(self) -> dict:
        with self.state.lock:
            lap = self.state.player(self.state.lap)
            return {"current_lap": lap["currentLapNum"] if lap else 0,
                    "laps": self.logger._laps_table_locked(),
                    "trace_laps": sorted(self.logger.by_lap.keys())}

    def session_json(self) -> dict:
        with self.state.lock:
            return self.logger._session_info_locked()

    def trace_json(self, lap_num: int, start: int) -> dict:
        with self.state.lock:
            cur = self.state.player(self.state.lap)
            current = cur["currentLapNum"] if cur else 0
            rows = self.logger.by_lap.get(lap_num, [])
            chunk = rows[start:]
            return {"lap": lap_num, "from": start, "rows": chunk, "next": start + len(chunk),
                    "complete": lap_num < current, "columns": SessionLogger.TRACE_COLUMNS}


    # ---- corner names (per track, editable from the dashboard)

    def _load_corner_names(self) -> dict:
        try:
            if CORNER_NAMES_FILE.exists():
                data = json.loads(CORNER_NAMES_FILE.read_text())
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError) as e:
            print(f"(could not read {CORNER_NAMES_FILE.name}: {e})")
        return {}

    def _track_length(self, track: str) -> float | None:
        with self.state.lock:
            s = self.state.session
            if s and TRACKS.get(s["trackId"]) == track and s["trackLength"]:
                return float(s["trackLength"])
        return None

    def corner_names_json(self, track: str, track_length: float | None = None) -> dict:
        """Custom names (yours) plus the built-in turn list for the track, both in metres."""
        saved = self._load_corner_names()
        custom = []
        for item in saved.get(track) or []:
            try:
                custom.append([float(item[0]), str(item[1])])
            except (TypeError, ValueError, IndexError):
                continue
        custom.sort()
        length = track_length or self._track_length(track)
        preset = []
        if length:
            for frac, label in TRACK_TURNS.get(track, []):
                preset.append([round(frac * length, 1), label])
        return {"track": track, "names": custom, "preset": preset, "track_length": length,
                "has_preset": track in TRACK_TURNS, "custom": bool(custom)}

    def save_corner_names(self, track: str, names: list) -> dict:
        if not track:
            return {"error": "no track given"}
        clean = []
        for item in names or []:
            try:
                dist, name = float(item[0]), str(item[1]).strip()[:40]
            except (TypeError, ValueError, IndexError):
                continue
            if name:
                clean.append([round(dist, 1), name])
        clean.sort()
        saved = self._load_corner_names()
        if clean:
            saved[track] = clean
        else:
            saved.pop(track, None)
        try:
            CORNER_NAMES_FILE.write_text(json.dumps(saved, indent=1, ensure_ascii=False))
        except OSError as e:
            return {"error": f"could not save: {e}"}
        return {"track": track, "names": clean, "saved": True}

    # ---- saved sessions (read back from the sessions folder)

    def _saved_dir(self, sid: str) -> Path | None:
        if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
            return None
        d = self.logger.root / sid
        return d if d.is_dir() else None

    def sessions_json(self) -> dict:
        out = []
        root = self.logger.root
        if root.is_dir():
            for d in sorted(root.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                info = {}
                try:
                    if (d / "session.json").exists():
                        info = json.loads((d / "session.json").read_text())
                except (OSError, ValueError):
                    pass
                laps = 0
                try:
                    if (d / "laps.csv").exists():
                        with open(d / "laps.csv", newline="") as fh:
                            laps = max(0, sum(1 for _ in fh) - 1)
                except OSError:
                    pass
                out.append({"id": d.name, "track": info.get("track"), "session_type": info.get("session_type"),
                            "recorded": info.get("recorded"), "best_lap": info.get("best_lap"), "laps": laps,
                            "live": self.logger.folder is not None and d == self.logger.folder})
        return {"sessions": out}

    def saved_json(self, sid: str) -> dict:
        d = self._saved_dir(sid)
        if not d:
            return {"error": "no such session"}
        info, laps = {}, []
        if (d / "session.json").exists():
            info = json.loads((d / "session.json").read_text())
        if (d / "laps.csv").exists():
            with open(d / "laps.csv", newline="") as fh:
                laps = [{k: _num(v) for k, v in row.items()} for row in csv.DictReader(fh)]
        return {"id": sid, "session": info, "laps": laps, "trace_laps": sorted(self._saved_trace(d).keys())}

    _trace_cache: dict = {}

    def _saved_trace(self, d: Path) -> dict:
        """Parses trace.csv once (per folder + file size) into {lap: rows}."""
        p = d / "trace.csv"
        if not p.exists():
            return {}
        key = (str(p), p.stat().st_size)
        cached = DashboardServer._trace_cache.get(str(p))
        if cached and cached[0] == key:
            return cached[1]
        by_lap: dict[int, list] = {}
        with open(p, newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd, None)
            if header:
                lap_i = header.index("lap") if "lap" in header else 1
                for row in rd:
                    vals = [_num(v) for v in row]
                    try:
                        by_lap.setdefault(int(vals[lap_i]), []).append(vals)
                    except (TypeError, ValueError):
                        continue
        DashboardServer._trace_cache = {str(p): (key, by_lap, header)}
        return by_lap

    def debug_json(self, sid: str) -> dict:
        d = self._saved_dir(sid)
        if not d:
            return {"error": "no such session", "root": str(self.logger.root)}
        out: dict = {"folder": str(d), "files": {}}
        for name in ("laps.csv", "trace.csv", "session.json"):
            p = d / name
            out["files"][name] = {"exists": p.exists(), "bytes": p.stat().st_size if p.exists() else 0}
        try:
            by_lap = self._saved_trace(d)
            out["trace_rows_per_lap"] = {str(k): len(v) for k, v in by_lap.items()}
            with open(d / "trace.csv", newline="") as fh:
                out["trace_header"] = next(csv.reader(fh), None)
        except Exception as e:  # noqa: BLE001
            out["trace_error"] = f"{type(e).__name__}: {e}"
        return out

    def saved_trace_json(self, sid: str, lap: int) -> dict:
        d = self._saved_dir(sid)
        if not d:
            return {"error": "no such session"}
        by_lap = self._saved_trace(d)
        cached = DashboardServer._trace_cache.get(str(d / "trace.csv"))
        columns = cached[2] if cached and cached[2] else SessionLogger.TRACE_COLUMNS
        rows = by_lap.get(lap, [])
        return {"lap": lap, "from": 0, "rows": rows, "next": len(rows), "complete": True, "columns": columns}


def _num(v):
    """CSV cell -> int / float / bool / str."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    t = v.strip()
    if t == "":
        return None
    if t in ("True", "False"):
        return t == "True"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", errors="replace")
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


# ----------------------------------------------------------------------------- keyboard

def read_key() -> str:
    """Wait for one key press (no Return needed). The terminal must be the front window."""
    if _MSVCRT is not None:                       # Windows
        ch = _MSVCRT.getwch()
        if ch in ("\x00", "\xe0"):               # arrow / function key: swallow the second byte
            _MSVCRT.getwch()
            return ""
        return ch
    if not sys.stdin.isatty():                    # started without a terminal: just keep running
        time.sleep(1)
        return ""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


# ----------------------------------------------------------------------------- modes

def run_test(state: RaceState, listener: TelemetryListener) -> None:
    print(f"Listening for the game on UDP port {PORT}. Press Ctrl+C to stop.")
    print("On the Xbox: Settings → Telemetry → UDP Telemetry ON, Broadcast Mode ON, port 20777, format 2026.\n")
    quiet_reported = False
    try:
        while True:
            time.sleep(1)
            if listener.error:
                print(f"ERROR: {listener.error}")
                return
            if state.seconds_since_packet() > 5:
                if not quiet_reported:
                    print("No packets yet. Is the game in a session (not the menus) with UDP telemetry on?")
                    quiet_reported = True
                continue
            quiet_reported = False
            print(state.one_line_status())
    except KeyboardInterrupt:
        print(f"\nStopped. {state.packets_received} packets received, {state.bad_packets} ignored.")


def run_mic_test(speaker: Speaker) -> None:
    transcriber = Transcriber()
    recorder = Recorder()
    print("\nRecording 4 seconds — say something like 'radio check, how are the tyres' ...")
    recorder.start()
    time.sleep(4)
    audio = recorder.stop()
    print(f"Got {len(audio) / SAMPLE_RATE:.1f}s of audio, peak level {float(abs(audio).max()) if len(audio) else 0:.2f} "
          f"(0.00 means the mic is muted or macOS blocked it)")
    text = transcriber.transcribe(audio)
    print(f"Whisper heard: {text!r}")
    speaker.say(f"I heard: {text}" if text else "I didn't hear anything.", "TEST")
    speaker.wait()


def run_engineer(state: RaceState, speaker: Speaker, text_mode: bool, claude_on: bool,
                 open_browser: bool = True, radio_on: bool = True) -> None:
    engineer = transcriber = recorder = None
    if claude_on:
        engineer = Engineer(state)
        if not text_mode:
            transcriber = Transcriber()
            recorder = Recorder()
    logger = SessionLogger(state)
    callouts = Callouts(state, speaker) if radio_on else None
    if callouts:
        callouts.start()
    dashboard = DashboardServer(state, logger)

    if radio_on:
        speaker.say(f"Radio check, {DRIVER_NAME}. Engineer online.")
    print(f"Recording to: {SESSIONS_DIR}")
    print(f"Live dashboard: {dashboard.url}")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(dashboard.url)
        except Exception:
            pass
    bits = ["SPACE = talk / stop"] if claude_on else []
    bits += ["S = status", "L = last lap", "Q = quit and save"]
    print("Controls:  " + "   ".join(bits))
    if not radio_on:
        print("Radio is off (--no-voice): no callouts, nothing else printed here unless something breaks.")
    if text_mode:
        print("Text mode: type a question and press Return.\n")

    def handle_question(question: str) -> None:
        if not question:
            speaker.say("Didn't catch that, say again.")
            return
        print(f"🎙️  {DRIVER_NAME}: {question}")
        try:
            answer = engineer.ask(question)  # type: ignore[union-attr]
        except Exception as e:
            speaker.say(f"Radio's down: {type(e).__name__}. Check the terminal.")
            print(f"API error: {e}")
            return
        speaker.say(answer)

    def last_lap() -> None:
        with state.lock:
            lap = state.player(state.lap)
            best = state.best_lap_ms(state.player_idx)
        if not lap or not lap["lastLapTimeInMS"]:
            speaker.say("No completed lap yet.", "STATUS")
            return
        msg = f'Last lap {fmt_lap_spoken(lap["lastLapTimeInMS"])}'
        if best:
            msg += f', best {fmt_lap_spoken(best)}'
        speaker.say(msg + ".", "STATUS")

    recording = False
    record_started = 0.0
    try:
        while True:
            if text_mode and claude_on:
                q = input("> ").strip()
                if q.lower() in ("q", "quit", "exit"):
                    break
                if q.lower() == "s":
                    speaker.say(state.quick_status(), "STATUS")
                elif q.lower() == "l":
                    last_lap()
                else:
                    handle_question(q)
                continue

            key = read_key()
            if key.lower() == "q":
                break
            if key.lower() == "s" and not recording:
                speaker.say(state.quick_status(), "STATUS")
            elif key.lower() == "l" and not recording:
                last_lap()
            elif key in (" ", "\n", "\r"):
                if not claude_on:
                    print("(voice questions need an ANTHROPIC_API_KEY in .env — callouts and recording still work)")
                    continue
                if not recording:
                    recorder.start()  # type: ignore[union-attr]
                    recording = True
                    record_started = time.time()
                    print("🔴 Listening... tap SPACE when you're done talking.")
                else:
                    audio = recorder.stop()  # type: ignore[union-attr]
                    recording = False
                    if time.time() - record_started > MAX_RECORD_SECONDS:
                        audio = audio[: MAX_RECORD_SECONDS * SAMPLE_RATE]
                    if len(audio) < SAMPLE_RATE // 2:
                        print("(too short, ignored)")
                        continue
                    print("… transcribing")
                    handle_question(transcriber.transcribe(audio))  # type: ignore[union-attr]
    except KeyboardInterrupt:
        pass
    finally:
        if callouts:
            callouts.stop()
        if recording and recorder:
            recorder.stop()
        folder = logger.flush()
        if folder:
            print(f"\nSession saved to: {folder}")
            print("Drag laps.csv, trace.csv and session.json from that folder into the chat for a debrief.")
            if sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", str(folder)], check=False)
        else:
            print("\nNothing recorded (no session data arrived).")
        print("Engineer offline.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice race engineer for EA F1 25/26")
    parser.add_argument("--test", action="store_true", help="show live telemetry from the game and exit with Ctrl+C")
    parser.add_argument("--mic-test", action="store_true", help="record 4s, transcribe, read it back")
    parser.add_argument("--text", action="store_true", help="type questions instead of speaking them")
    parser.add_argument("--no-voice", action="store_true", help="turn the radio off entirely: no speech and no callouts")
    parser.add_argument("--no-claude", action="store_true", help="ignore any API key; callouts and recording only")
    parser.add_argument("--no-browser", action="store_true", help="don't open the dashboard in the browser automatically")
    args = parser.parse_args()
    claude_on = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.no_claude

    if sys.platform != "darwin" and not args.no_voice:
        print("Note: voice output uses the macOS 'say' command; on other systems replies are text only.")

    speaker = Speaker(voice=VOICE, rate=SPEECH_RATE, enabled=not args.no_voice and sys.platform == "darwin")

    if args.mic_test:
        run_mic_test(speaker)
        return

    state = RaceState()
    listener = TelemetryListener(state, port=PORT)
    listener.start()
    time.sleep(0.3)
    if listener.error:
        raise SystemExit(f"ERROR: {listener.error}")

    if args.test:
        run_test(state, listener)
        return

    run_engineer(state, speaker, text_mode=args.text, claude_on=claude_on,
                 open_browser=not args.no_browser, radio_on=not args.no_voice)


if __name__ == "__main__":
    main()
