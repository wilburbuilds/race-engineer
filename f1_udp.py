"""
f1_udp.py — listener + parser for EA SPORTS F1 25 / F1 26 telemetry (UDP packet format 2026).

Packet layouts follow the official F1 26 UDP specification (packet format 2026, 24 cars).
All values are little-endian and packed with no padding.

Nothing in here talks to a microphone or to Claude. It just listens on a UDP port,
decodes what the game sends, and keeps the latest picture of the session in RaceState.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections import deque
from typing import Any

PACKET_FORMAT = 2026
MAX_CARS = 24
DEFAULT_PORT = 20777


# ---------------------------------------------------------------------------
# Small helper for packed C structs
# ---------------------------------------------------------------------------

def _nvals(code: str) -> int:
    """How many Python values a struct code such as '4f', '32s' or 'H' produces."""
    digits = ""
    for ch in code:
        if ch.isdigit():
            digits += ch
        else:
            break
    kind = code[len(digits):]
    if kind == "s":
        return 1
    return int(digits) if digits else 1


class Layout:
    """A packed little-endian record described by (name, struct code) pairs."""

    def __init__(self, fields: list[tuple[str, str]]):
        self.fields = fields
        self.struct = struct.Struct("<" + "".join(code for _, code in fields))
        self.size = self.struct.size
        self._counts = [_nvals(code) for _, code in fields]

    def unpack(self, buf: bytes, offset: int = 0) -> dict[str, Any]:
        vals = self.struct.unpack_from(buf, offset)
        out: dict[str, Any] = {}
        i = 0
        for (name, _), n in zip(self.fields, self._counts):
            out[name] = vals[i] if n == 1 else vals[i:i + n]
            i += n
        return out

    def unpack_array(self, buf: bytes, offset: int, count: int) -> list[dict[str, Any]]:
        return [self.unpack(buf, offset + k * self.size) for k in range(count)]


# ---------------------------------------------------------------------------
# Packet layouts (F1 26 UDP spec, format 2026)
# ---------------------------------------------------------------------------

HEADER = Layout([
    ("packetFormat", "H"), ("gameYear", "B"), ("gameMajorVersion", "B"), ("gameMinorVersion", "B"),
    ("packetVersion", "B"), ("packetId", "B"), ("sessionUID", "Q"), ("sessionTime", "f"),
    ("frameIdentifier", "I"), ("overallFrameIdentifier", "I"),
    ("playerCarIndex", "B"), ("secondaryPlayerCarIndex", "B"),
])  # 29 bytes

# Packet IDs
PKT_MOTION, PKT_SESSION, PKT_LAP, PKT_EVENT, PKT_PARTICIPANTS, PKT_SETUPS = 0, 1, 2, 3, 4, 5
PKT_TELEMETRY, PKT_STATUS, PKT_FINAL, PKT_LOBBY, PKT_DAMAGE, PKT_HISTORY = 6, 7, 8, 9, 10, 11
PKT_TYRE_SETS, PKT_MOTION_EX, PKT_TIME_TRIAL, PKT_LAP_POSITIONS, PKT_TELEMETRY2 = 12, 13, 14, 15, 16

MOTION_POS = Layout([("worldPositionX", "f"), ("worldPositionY", "f"), ("worldPositionZ", "f")])

MARSHAL_ZONE = Layout([("zoneStart", "f"), ("zoneFlag", "b")])
WEATHER_SAMPLE = Layout([
    ("sessionType", "B"), ("timeOffset", "B"), ("weather", "B"), ("trackTemperature", "b"),
    ("trackTemperatureChange", "b"), ("airTemperature", "b"), ("airTemperatureChange", "b"),
    ("rainPercentage", "B"),
])
AERO_ZONE = Layout([("zoneStart", "f"), ("zoneEnd", "f")])

SESSION_A = Layout([
    ("weather", "B"), ("trackTemperature", "b"), ("airTemperature", "b"), ("totalLaps", "B"),
    ("trackLength", "H"), ("sessionType", "B"), ("trackId", "b"), ("formula", "B"),
    ("sessionTimeLeft", "H"), ("sessionDuration", "H"), ("pitSpeedLimit", "B"), ("gamePaused", "B"),
    ("isSpectating", "B"), ("spectatorCarIndex", "B"), ("sliProNativeSupport", "B"),
    ("numMarshalZones", "B"),
])
SESSION_B = Layout([("safetyCarStatus", "B"), ("networkGame", "B"), ("numWeatherForecastSamples", "B")])
SESSION_C = Layout([
    ("forecastAccuracy", "B"), ("aiDifficulty", "B"), ("seasonLinkIdentifier", "I"),
    ("weekendLinkIdentifier", "I"), ("sessionLinkIdentifier", "I"), ("pitStopWindowIdealLap", "B"),
    ("pitStopWindowLatestLap", "B"), ("pitStopRejoinPosition", "B"), ("steeringAssist", "B"),
    ("brakingAssist", "B"), ("gearboxAssist", "B"), ("pitAssist", "B"), ("pitReleaseAssist", "B"),
    ("ERSAssist", "B"), ("DRSAssist", "B"), ("dynamicRacingLine", "B"), ("dynamicRacingLineType", "B"),
    ("gameMode", "B"), ("ruleSet", "B"), ("timeOfDay", "I"), ("sessionLength", "B"),
    ("speedUnitsLeadPlayer", "B"), ("temperatureUnitsLeadPlayer", "B"),
    ("speedUnitsSecondaryPlayer", "B"), ("temperatureUnitsSecondaryPlayer", "B"),
    ("numSafetyCarPeriods", "B"), ("numVirtualSafetyCarPeriods", "B"), ("numRedFlagPeriods", "B"),
    ("equalCarPerformance", "B"), ("recoveryMode", "B"), ("flashbackLimit", "B"), ("surfaceType", "B"),
    ("lowFuelMode", "B"), ("raceStarts", "B"), ("tyreTemperature", "B"), ("pitLaneTyreSim", "B"),
    ("carDamage", "B"), ("carDamageRate", "B"), ("collisions", "B"), ("collisionsOffForFirstLapOnly", "B"),
    ("mpUnsafePitRelease", "B"), ("mpOffForGriefing", "B"), ("cornerCuttingStringency", "B"),
    ("parcFermeRules", "B"), ("pitStopExperience", "B"), ("safetyCar", "B"), ("safetyCarExperience", "B"),
    ("formationLap", "B"), ("formationLapExperience", "B"), ("redFlags", "B"),
    ("affectsLicenceLevelSolo", "B"), ("affectsLicenceLevelMP", "B"), ("numSessionsInWeekend", "B"),
    ("weekendStructure", "12B"), ("sector2LapDistanceStart", "f"), ("sector3LapDistanceStart", "f"),
    ("activeAeroTrackStatus", "B"), ("numActiveAeroZonesFull", "B"),
])
SESSION_D = Layout([("numActiveAeroZonesPartial", "B")])
SESSION_E = Layout([("numDRSZones", "B")])
SESSION_F = Layout([
    ("startReactionTime", "f"), ("antiLockBrakesAssist", "B"), ("tractionControlAssist", "B"),
    ("dynamicRacingLineHiVis", "B"), ("dynamicRacingLineColourBlind", "B"), ("recurringRewindPrompt", "B"),
])

LAP_DATA = Layout([
    ("lastLapTimeInMS", "I"), ("currentLapTimeInMS", "I"),
    ("sector1TimeMSPart", "H"), ("sector1TimeMinutesPart", "B"),
    ("sector2TimeMSPart", "H"), ("sector2TimeMinutesPart", "B"),
    ("deltaToCarInFrontMSPart", "H"), ("deltaToCarInFrontMinutesPart", "B"),
    ("deltaToRaceLeaderMSPart", "H"), ("deltaToRaceLeaderMinutesPart", "B"),
    ("lapDistance", "f"), ("totalDistance", "f"), ("safetyCarDelta", "f"),
    ("carPosition", "B"), ("currentLapNum", "B"), ("pitStatus", "B"), ("numPitStops", "B"),
    ("sector", "B"), ("currentLapInvalid", "B"), ("penalties", "B"), ("totalWarnings", "B"),
    ("cornerCuttingWarnings", "B"), ("numUnservedDriveThroughPens", "B"), ("numUnservedStopGoPens", "B"),
    ("gridPosition", "B"), ("driverStatus", "B"), ("resultStatus", "B"), ("pitLaneTimerActive", "B"),
    ("pitLaneTimeInLaneInMS", "H"), ("pitStopTimerInMS", "H"), ("pitStopShouldServePen", "B"),
    ("speedTrapFastestSpeed", "f"), ("speedTrapFastestLap", "B"),
])  # 57 bytes
LAP_TAIL = Layout([("timeTrialPBCarIdx", "B"), ("timeTrialRivalCarIdx", "B")])

PARTICIPANT = Layout([
    ("aiControlled", "B"), ("driverId", "H"), ("networkId", "H"), ("teamId", "H"), ("myTeam", "B"),
    ("raceNumber", "B"), ("nationality", "B"), ("name", "32s"), ("yourTelemetry", "B"),
    ("showOnlineNames", "B"), ("techLevel", "H"), ("platform", "B"), ("numColours", "B"),
    ("liveryColours", "12B"),
])  # 60 bytes

CAR_SETUP = Layout([
    ("frontWing", "B"), ("rearWing", "B"), ("onThrottle", "B"), ("offThrottle", "B"),
    ("frontCamber", "f"), ("rearCamber", "f"), ("frontToe", "f"), ("rearToe", "f"),
    ("frontSuspension", "B"), ("rearSuspension", "B"), ("frontAntiRollBar", "B"), ("rearAntiRollBar", "B"),
    ("frontSuspensionHeight", "B"), ("rearSuspensionHeight", "B"), ("brakePressure", "B"),
    ("brakeBias", "B"), ("engineBraking", "B"),
    ("rearLeftTyrePressure", "f"), ("rearRightTyrePressure", "f"),
    ("frontLeftTyrePressure", "f"), ("frontRightTyrePressure", "f"),
    ("ballast", "B"), ("fuelLoad", "f"),
])  # 50 bytes

CAR_TELEMETRY = Layout([
    ("speed", "H"), ("throttle", "f"), ("steer", "f"), ("brake", "f"), ("clutch", "B"), ("gear", "b"),
    ("engineRPM", "H"), ("drs", "B"), ("revLightsPercent", "B"), ("revLightsBitValue", "H"),
    ("brakesTemperature", "4H"), ("tyresSurfaceTemperature", "4B"), ("tyresInnerTemperature", "4B"),
    ("engineTemperature", "B"), ("tyresPressure", "4f"), ("surfaceType", "4B"),
])  # 59 bytes
TELEMETRY_TAIL = Layout([("mfdPanelIndex", "B"), ("mfdPanelIndexSecondaryPlayer", "B"), ("suggestedGear", "b")])

CAR_STATUS = Layout([
    ("tractionControl", "B"), ("antiLockBrakes", "B"), ("fuelMix", "B"), ("frontBrakeBias", "B"),
    ("pitLimiterStatus", "B"), ("fuelInTank", "f"), ("fuelCapacity", "f"), ("fuelRemainingLaps", "f"),
    ("maxRPM", "H"), ("idleRPM", "H"), ("maxGears", "B"), ("drsAllowed", "B"),
    ("drsActivationDistance", "H"), ("actualTyreCompound", "B"), ("visualTyreCompound", "B"),
    ("tyresAgeLaps", "B"), ("vehicleFiaFlags", "b"), ("enginePowerICE", "f"), ("enginePowerMGUK", "f"),
    ("ersStoreEnergy", "f"), ("ersDeployMode", "B"), ("ersHarvestedThisLapMGUK", "f"),
    ("ersHarvestedThisLapMGUH", "f"), ("ersHarvestLimitPerLap", "f"), ("ersDeployedThisLap", "f"),
    ("networkPaused", "B"),
])  # 59 bytes

CAR_DAMAGE = Layout([
    ("tyresWear", "4f"), ("tyresDamage", "4B"), ("brakesDamage", "4B"), ("tyreBlisters", "4B"),
    ("frontLeftWingDamage", "B"), ("frontRightWingDamage", "B"), ("rearWingDamage", "B"),
    ("floorDamage", "B"), ("diffuserDamage", "B"), ("sidepodDamage", "B"), ("drsFault", "B"),
    ("ersFault", "B"), ("gearBoxDamage", "B"), ("engineDamage", "B"), ("engineMGUHWear", "B"),
    ("engineESWear", "B"), ("engineCEWear", "B"), ("engineICEWear", "B"), ("engineMGUKWear", "B"),
    ("engineTCWear", "B"), ("engineBlown", "B"), ("engineSeized", "B"),
])  # 46 bytes

HISTORY_HEAD = Layout([
    ("carIdx", "B"), ("numLaps", "B"), ("numTyreStints", "B"), ("bestLapTimeLapNum", "B"),
    ("bestSector1LapNum", "B"), ("bestSector2LapNum", "B"), ("bestSector3LapNum", "B"),
])
LAP_HISTORY = Layout([
    ("lapTimeInMS", "I"), ("sector1TimeMSPart", "H"), ("sector1TimeMinutes", "B"),
    ("sector2TimeMSPart", "H"), ("sector2TimeMinutes", "B"), ("sector3TimeMSPart", "H"),
    ("sector3TimeMinutes", "B"), ("lapValidBitFlags", "B"),
])  # 14 bytes
TYRE_STINT = Layout([("endLap", "B"), ("tyreActualCompound", "B"), ("tyreVisualCompound", "B")])

TYRE_SET = Layout([
    ("actualTyreCompound", "B"), ("visualTyreCompound", "B"), ("wear", "B"), ("available", "B"),
    ("recommendedSession", "B"), ("lifeSpan", "B"), ("usableLife", "B"), ("lapDeltaTime", "h"),
    ("fitted", "B"),
])  # 10 bytes

TIME_TRIAL_SET = Layout([
    ("carIdx", "B"), ("teamId", "H"), ("lapTimeInMS", "I"), ("sector1TimeInMS", "I"),
    ("sector2TimeInMS", "I"), ("sector3TimeInMS", "I"), ("tractionControl", "B"), ("gearboxAssist", "B"),
    ("antiLockBrakes", "B"), ("equalCarPerformance", "B"), ("customSetup", "B"), ("valid", "B"),
])  # 25 bytes

CAR_TELEMETRY2 = Layout([
    ("activeAeroMode", "B"), ("activeAeroAvailable", "B"), ("activeAeroActivationDistance", "H"),
    ("overtakeAvailable", "B"), ("overtakeActive", "B"), ("overtakeActivationDistance", "H"),
    ("regulations2026", "B"), ("drivingWrongWay", "B"),
])  # 10 bytes

FINAL_CLASSIFICATION = Layout([
    ("position", "B"), ("numLaps", "B"), ("gridPosition", "B"), ("points", "B"), ("numPitStops", "B"),
    ("resultStatus", "B"), ("resultReason", "B"), ("bestLapTimeInMS", "I"), ("totalRaceTime", "d"),
    ("penaltiesTime", "B"), ("numPenalties", "B"), ("numTyreStints", "B"),
    ("tyreStintsActual", "8B"), ("tyreStintsVisual", "8B"), ("tyreStintsEndLaps", "8B"),
])  # 46 bytes

# Expected total packet sizes from the spec — used by the self-test and to reject junk.
EXPECTED_SIZES = {
    PKT_MOTION: 1325, PKT_SESSION: 926, PKT_LAP: 1399, PKT_EVENT: 45, PKT_PARTICIPANTS: 1470,
    PKT_SETUPS: 1233, PKT_TELEMETRY: 1448, PKT_STATUS: 1445, PKT_FINAL: 1134, PKT_LOBBY: 1062,
    PKT_DAMAGE: 1133, PKT_HISTORY: 1460, PKT_TYRE_SETS: 231, PKT_MOTION_EX: 273,
    PKT_TIME_TRIAL: 104, PKT_LAP_POSITIONS: 1231, PKT_TELEMETRY2: 269,
}


# ---------------------------------------------------------------------------
# Lookup tables (from the spec appendices)
# ---------------------------------------------------------------------------

TRACKS = {
    0: "Melbourne", 2: "Shanghai", 3: "Bahrain", 4: "Barcelona", 5: "Monaco", 6: "Montreal",
    7: "Silverstone", 9: "Hungaroring", 10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka",
    14: "Abu Dhabi", 15: "Austin", 16: "Interlagos", 17: "Austria", 19: "Mexico", 20: "Baku",
    26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Qatar",
    39: "Silverstone (Reverse)", 40: "Austria (Reverse)", 41: "Zandvoort (Reverse)", 42: "Madrid",
}
SESSION_TYPES = {
    0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
    9: "One-Shot Qualifying", 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Short Sprint Shootout", 14: "One-Shot Sprint Shootout",
    15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial",
}
RACE_SESSIONS = {15, 16, 17}
WEATHER = {0: "clear", 1: "light cloud", 2: "overcast", 3: "light rain", 4: "heavy rain", 5: "storm"}
SAFETY_CAR = {0: "none", 1: "FULL SAFETY CAR", 2: "VIRTUAL SAFETY CAR", 3: "formation lap"}
FLAGS = {-1: "unknown", 0: "none", 1: "green", 2: "BLUE", 3: "YELLOW"}
ACTUAL_COMPOUND = {
    16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "Inter", 8: "Wet",
    9: "Dry (classic)", 10: "Wet (classic)", 11: "SuperSoft (F2)", 12: "Soft (F2)",
    13: "Medium (F2)", 14: "Hard (F2)", 15: "Wet (F2)",
}
VISUAL_COMPOUND = {16: "Soft", 17: "Medium", 18: "Hard", 7: "Inter", 8: "Wet",
                   19: "SuperSoft", 20: "Soft", 21: "Medium", 22: "Hard", 15: "Wet"}
FUEL_MIX = {0: "lean", 1: "standard", 2: "rich", 3: "max"}
ERS_MODE = {0: "none", 1: "medium", 2: "hotlap", 3: "overtake"}
DRIVER_STATUS = {0: "in garage", 1: "flying lap", 2: "in lap", 3: "out lap", 4: "on track"}
PIT_STATUS = {0: "", 1: "PITTING", 2: "IN PIT BOX"}
TEAMS = {
    0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin", 5: "Alpine",
    6: "RB", 7: "Haas", 8: "McLaren", 9: "Sauber", 41: "F1 Generic", 104: "Custom Team",
    129: "Konnersport", 142: "APXGP", 154: "APXGP", 155: "Konnersport",
    476: "Mercedes", 477: "Ferrari", 478: "Red Bull", 479: "Williams", 480: "Aston Martin",
    481: "Alpine", 482: "RB", 483: "Haas", 484: "McLaren", 485: "Audi", 486: "Cadillac",
}
PENALTY_TYPES = {
    0: "drive through", 1: "stop-go", 2: "grid penalty", 3: "penalty reminder", 4: "time penalty",
    5: "warning", 6: "disqualified", 7: "removed from formation lap", 8: "parked too long",
    9: "tyre regulations", 10: "lap invalidated", 11: "this and next lap invalidated",
    12: "lap invalidated", 13: "this and next lap invalidated", 14: "this and previous lap invalidated",
    15: "this and previous lap invalidated", 16: "retired", 17: "black flag timer",
}
INFRINGEMENTS = {
    0: "blocking by slow driving", 1: "blocking by wrong-way driving", 2: "reversing off the start line",
    3: "big collision", 4: "small collision", 5: "collision, failed to hand back position",
    6: "collision, failed to hand back positions", 7: "corner cutting, gained time",
    8: "corner cutting overtake", 9: "corner cutting overtakes", 10: "crossed pit exit line",
    11: "ignoring blue flags", 12: "ignoring yellow flags", 13: "ignoring drive through",
    14: "too many drive throughs", 15: "drive through reminder", 16: "serve drive through this lap",
    17: "pit lane speeding", 18: "parked too long", 19: "ignoring tyre regulations",
    20: "too many penalties", 21: "multiple warnings", 22: "approaching disqualification",
    25: "corner cutting", 26: "running wide", 27: "ran wide, minor time gain",
    28: "ran wide, significant time gain", 29: "ran wide, extreme time gain", 30: "wall riding",
    31: "flashback used", 32: "reset to track", 33: "blocking the pit lane", 34: "jump start",
    35: "safety car collision", 36: "illegal overtake under safety car",
    37: "exceeding safety car pace", 38: "exceeding VSC pace", 39: "formation lap too slow",
    40: "formation lap parking", 43: "falling too far back behind safety car",
    47: "engine component change", 48: "gearbox change", 49: "parc ferme change",
    52: "illegal time gain", 53: "mandatory pit stop",
}
WHEELS = ("RL", "RR", "FL", "FR")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_lap(ms: int | float | None) -> str:
    """1:13.412 style. Returns '--' for 0/None."""
    if not ms or ms <= 0:
        return "--"
    ms = int(round(ms))
    m, rem = divmod(ms, 60000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}" if m else f"{s}.{milli:03d}"


def fmt_lap_spoken(ms: int | float | None) -> str:
    """Radio style, to the tenth: '1 13.4' so the voice reads 'one thirteen point four'."""
    if not ms or ms <= 0:
        return "no time"
    ms = int(round(ms))
    m, rem = divmod(ms, 60000)
    s, milli = divmod(rem, 1000)
    tenth = milli // 100
    return f"{m} {s:02d}.{tenth}" if m else f"{s}.{tenth}"


def fmt_gap(ms: int | float | None) -> str:
    if ms is None:
        return "--"
    return f"{ms / 1000:.1f}s"


def sector_ms(ms_part: int, minutes_part: int) -> int:
    return minutes_part * 60000 + ms_part


def _name(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Race state
# ---------------------------------------------------------------------------

class RaceState:
    """Latest decoded picture of the session. Thread-safe via self.lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()
        # Counters that survive session resets
        self.packets_received = 0
        self.bad_packets = 0
        self.last_packet_time = 0.0
        self.last_sender = ""
        self.packet_format: int | None = None
        self.game_version = ""
        self._rate_window: deque[float] = deque()
        # Optional hooks, both called while self.lock is held (so they must not take it again):
        #   on_packet(packet_id)  after each decoded packet
        #   on_session_end()      just before the state is wiped for a new session
        self.on_packet = None
        self.on_session_end = None

    def reset(self) -> None:
        self.session_uid: int | None = None
        self.player_idx: int | None = None
        self.session: dict | None = None
        self.lap: list[dict | None] = [None] * MAX_CARS
        self.lap_tail: dict | None = None
        self.telemetry: list[dict | None] = [None] * MAX_CARS
        self.telemetry_tail: dict | None = None
        self.telemetry2: list[dict | None] = [None] * MAX_CARS
        self.status: list[dict | None] = [None] * MAX_CARS
        self.damage: list[dict | None] = [None] * MAX_CARS
        self.setup: list[dict | None] = [None] * MAX_CARS
        self.participants: list[dict | None] = [None] * MAX_CARS
        self.num_active_cars = 0
        self.history: dict[int, dict] = {}
        self.motion: dict | None = None
        self.tyre_sets: dict | None = None
        self.time_trial: dict | None = None
        self.final: dict | None = None
        self.events: deque[dict] = deque(maxlen=200)
        self.session_time = 0.0

    # ---------------------------------------------------------------- ingest

    def ingest(self, data: bytes, sender: str = "") -> None:
        if len(data) < HEADER.size:
            self.bad_packets += 1
            return
        h = HEADER.unpack(data)
        now = time.time()
        with self.lock:
            self.packets_received += 1
            self.last_packet_time = now
            self.last_sender = sender
            self.packet_format = h["packetFormat"]
            self.game_version = f'{h["gameMajorVersion"]}.{h["gameMinorVersion"]:02d}'
            self._rate_window.append(now)
            while self._rate_window and now - self._rate_window[0] > 1.0:
                self._rate_window.popleft()

            if h["packetFormat"] != PACKET_FORMAT:
                self.bad_packets += 1
                return
            if h["sessionUID"] == 0:      # menu / lobby chatter — not a session, must not wipe the last one
                return

            pid = h["packetId"]
            expected = EXPECTED_SIZES.get(pid)
            if expected is not None and len(data) != expected:
                self.bad_packets += 1
                return

            if self.session_uid != h["sessionUID"]:
                if self.session_uid is not None and self.on_session_end is not None:
                    try:
                        self.on_session_end()
                    except Exception as e:
                        print(f"(logger error: {e})")
                self.reset()
                self.session_uid = h["sessionUID"]
            self.player_idx = h["playerCarIndex"]
            self.session_time = h["sessionTime"]

            try:
                self._parse(pid, data)
            except struct.error:
                self.bad_packets += 1
                return
            if self.on_packet is not None:
                try:
                    self.on_packet(pid)
                except Exception as e:  # a logging bug must never stop the radio
                    print(f"(logger error: {e})")

    def _parse(self, pid: int, data: bytes) -> None:
        off = HEADER.size
        if pid == PKT_SESSION:
            self.session = self._parse_session(data, off)
        elif pid == PKT_LAP:
            self.lap = LAP_DATA.unpack_array(data, off, MAX_CARS)
            self.lap_tail = LAP_TAIL.unpack(data, off + MAX_CARS * LAP_DATA.size)
        elif pid == PKT_EVENT:
            ev = self._parse_event(data, off)
            self.latest_event = ev
            if ev and ev['code'] != 'BUTN':
                self.events.append(ev)
        elif pid == PKT_PARTICIPANTS:
            self.num_active_cars = data[off]
            parts = PARTICIPANT.unpack_array(data, off + 1, MAX_CARS)
            for p in parts:
                p["name"] = _name(p["name"])
            self.participants = parts
        elif pid == PKT_SETUPS:
            self.setup = CAR_SETUP.unpack_array(data, off, MAX_CARS)
        elif pid == PKT_TELEMETRY:
            self.telemetry = CAR_TELEMETRY.unpack_array(data, off, MAX_CARS)
            self.telemetry_tail = TELEMETRY_TAIL.unpack(data, off + MAX_CARS * CAR_TELEMETRY.size)
        elif pid == PKT_STATUS:
            self.status = CAR_STATUS.unpack_array(data, off, MAX_CARS)
        elif pid == PKT_DAMAGE:
            self.damage = CAR_DAMAGE.unpack_array(data, off, MAX_CARS)
        elif pid == PKT_HISTORY:
            head = HISTORY_HEAD.unpack(data, off)
            off += HISTORY_HEAD.size
            laps = LAP_HISTORY.unpack_array(data, off, head["numLaps"])
            off += LAP_HISTORY.size * 100
            stints = TYRE_STINT.unpack_array(data, off, head["numTyreStints"])
            head["laps"] = laps
            head["stints"] = stints
            self.history[head["carIdx"]] = head
        elif pid == PKT_TYRE_SETS:
            car_idx = data[off]
            sets = TYRE_SET.unpack_array(data, off + 1, 20)
            fitted = data[off + 1 + 20 * TYRE_SET.size]
            if car_idx == self.player_idx:
                self.tyre_sets = {"carIdx": car_idx, "sets": sets, "fittedIdx": fitted}
        elif pid == PKT_TIME_TRIAL:
            sets = TIME_TRIAL_SET.unpack_array(data, off, 3)
            self.time_trial = {"sessionBest": sets[0], "personalBest": sets[1], "rival": sets[2]}
        elif pid == PKT_TELEMETRY2:
            self.telemetry2 = CAR_TELEMETRY2.unpack_array(data, off, MAX_CARS)
        elif pid == PKT_FINAL:
            num = data[off]
            rows = FINAL_CLASSIFICATION.unpack_array(data, off + 1, MAX_CARS)
            self.final = {"numCars": num, "rows": rows}
        elif pid == PKT_MOTION:
            self.motion = self._parse_motion(data)
        # MotionEx, Lobby and LapPositions are ignored on purpose (not useful on the radio).

    def _parse_motion(self, data: bytes) -> dict | None:
        """World position of the player's car.

        Only the first three floats of each car's record are read (position is always first), with the
        record size worked out from the packet length, so the fields after it don't have to be known.
        """
        if self.player_idx is None:
            return None
        body = len(data) - HEADER.size
        stride, rem = divmod(body, MAX_CARS)
        if rem or stride < MOTION_POS.size:
            return None
        pos = MOTION_POS.unpack(data, HEADER.size + self.player_idx * stride)
        x, y, z = pos["worldPositionX"], pos["worldPositionY"], pos["worldPositionZ"]
        if not all(abs(v) < 1e5 and v == v for v in (x, y, z)):   # nonsense or NaN: ignore
            return None
        return {"x": x, "y": y, "z": z}

    def _parse_session(self, data: bytes, off: int) -> dict:
        s = SESSION_A.unpack(data, off)
        off += SESSION_A.size
        s["marshalZones"] = MARSHAL_ZONE.unpack_array(data, off, s["numMarshalZones"])
        off += MARSHAL_ZONE.size * 21
        s.update(SESSION_B.unpack(data, off))
        off += SESSION_B.size
        s["forecast"] = WEATHER_SAMPLE.unpack_array(data, off, min(s["numWeatherForecastSamples"], 64))
        off += WEATHER_SAMPLE.size * 64
        s.update(SESSION_C.unpack(data, off))
        off += SESSION_C.size
        s["activeAeroZonesFull"] = AERO_ZONE.unpack_array(data, off, min(s["numActiveAeroZonesFull"], 8))
        off += AERO_ZONE.size * 8
        s.update(SESSION_D.unpack(data, off))
        off += SESSION_D.size
        s["activeAeroZonesPartial"] = AERO_ZONE.unpack_array(data, off, min(s["numActiveAeroZonesPartial"], 8))
        off += AERO_ZONE.size * 8
        s.update(SESSION_E.unpack(data, off))
        off += SESSION_E.size
        s["drsZones"] = AERO_ZONE.unpack_array(data, off, min(s["numDRSZones"], 4))
        off += AERO_ZONE.size * 4
        s.update(SESSION_F.unpack(data, off))
        off += SESSION_F.size
        assert off == EXPECTED_SIZES[PKT_SESSION], off
        return s

    def _parse_event(self, data: bytes, off: int) -> dict | None:
        code = data[off:off + 4].decode("ascii", errors="replace")
        off += 4
        ev: dict[str, Any] = {"code": code, "time": time.time(), "sessionTime": self.session_time}
        u = lambda fmt: struct.unpack_from("<" + fmt, data, off)  # noqa: E731
        if code == "FTLP":
            ev["vehicleIdx"], ev["lapTime"] = u("Bf")
        elif code == "RTMT":
            ev["vehicleIdx"], ev["reason"] = u("BB")
        elif code == "DRSD":
            (ev["reason"],) = u("B")
        elif code in ("TMPT", "RCWN", "DTSV"):
            (ev["vehicleIdx"],) = u("B")
        elif code == "PENA":
            (ev["penaltyType"], ev["infringementType"], ev["vehicleIdx"], ev["otherVehicleIdx"],
             ev["timeSeconds"], ev["lapNum"], ev["placesGained"]) = u("BBBBBBB")
        elif code == "SPTP":
            (ev["vehicleIdx"], ev["speed"], ev["isOverallFastest"], ev["isDriverFastest"],
             ev["fastestVehicleIdx"], ev["fastestSpeed"]) = u("BfBBBf")
        elif code == "STLG":
            (ev["numLights"],) = u("B")
        elif code == "SGSV":
            ev["vehicleIdx"], ev["stopTime"] = u("Bf")
        elif code == "FLBK":
            ev["frameIdentifier"], ev["flashbackSessionTime"] = u("If")
        elif code == "BUTN":
            (ev["buttonStatus"],) = u("I")
        elif code == "OVTK":
            ev["overtakingIdx"], ev["overtakenIdx"] = u("BB")
        elif code == "SCAR":
            ev["safetyCarType"], ev["eventType"] = u("BB")
        elif code == "COLL":
            ev["vehicle1Idx"], ev["vehicle2Idx"], ev["severity"] = u("BBB")
        # SSTA, SEND, DRSE, CHQF, LGOT, RDFL carry no details
        return ev

    # ---------------------------------------------------------------- queries

    def _prune_rate(self) -> None:
        now = time.time()
        while self._rate_window and now - self._rate_window[0] > 1.0:
            self._rate_window.popleft()

    def packets_per_second(self) -> int:
        with self.lock:
            self._prune_rate()
            return len(self._rate_window)

    def seconds_since_packet(self) -> float:
        return time.time() - self.last_packet_time if self.last_packet_time else float("inf")

    def car_name(self, idx: int | None) -> str:
        if idx is None or idx >= MAX_CARS:
            return "unknown"
        p = self.participants[idx]
        if not p or not p["name"]:
            return f"car {idx}"
        team = TEAMS.get(p["teamId"])
        return f'{p["name"]} ({team})' if team else p["name"]

    def player(self, table: list) -> dict | None:
        if self.player_idx is None:
            return None
        return table[self.player_idx]

    def is_race(self) -> bool:
        return bool(self.session) and self.session["sessionType"] in RACE_SESSIONS

    def is_time_trial(self) -> bool:
        return bool(self.session) and self.session["sessionType"] == 18

    def speed_units(self) -> str:
        return "mph" if (self.session and self.session["speedUnitsLeadPlayer"] == 0) else "kph"

    def speed_conv(self, kph: float) -> float:
        return kph * 0.621371 if self.speed_units() == "mph" else kph

    def temp_str(self, celsius: float) -> str:
        if self.session and self.session["temperatureUnitsLeadPlayer"] == 1:
            return f"{celsius * 9 / 5 + 32:.0f}F"
        return f"{celsius:.0f}C"

    def player_history(self) -> dict | None:
        return self.history.get(self.player_idx) if self.player_idx is not None else None

    def best_lap_ms(self, idx: int | None, exclude_lap: int | None = None) -> int | None:
        """Best valid lap for a car from its session history, or None. exclude_lap is 1-based."""
        if idx is None:
            return None
        h = self.history.get(idx)
        if not h:
            return None
        valid = [l["lapTimeInMS"] for n, l in enumerate(h["laps"], start=1)
                 if l["lapTimeInMS"] > 0 and (l["lapValidBitFlags"] & 0x01) and n != exclude_lap]
        return min(valid) if valid else None

    def lap_valid(self, idx: int | None, lap_num: int) -> bool | None:
        """True/False for a completed lap's validity, None if unknown."""
        if idx is None:
            return None
        h = self.history.get(idx)
        if not h or lap_num < 1 or lap_num > len(h["laps"]):
            return None
        l = h["laps"][lap_num - 1]
        if l["lapTimeInMS"] <= 0:
            return None
        return bool(l["lapValidBitFlags"] & 0x01)

    def best_sectors_ms(self, idx: int | None) -> tuple[int | None, int | None, int | None]:
        if idx is None:
            return (None, None, None)
        h = self.history.get(idx)
        if not h:
            return (None, None, None)
        out: list[int | None] = []
        for n, bit in ((1, 0x02), (2, 0x04), (3, 0x08)):
            vals = [sector_ms(l[f"sector{n}TimeMSPart"], l[f"sector{n}TimeMinutes"]) for l in h["laps"]
                    if (l["lapValidBitFlags"] & bit) and sector_ms(l[f"sector{n}TimeMSPart"], l[f"sector{n}TimeMinutes"]) > 0]
            out.append(min(vals) if vals else None)
        return (out[0], out[1], out[2])

    def lap_sectors_ms(self, idx: int | None, lap_num: int) -> tuple[int, int, int] | None:
        """Sector times of a completed lap number (1-based) from session history."""
        if idx is None:
            return None
        h = self.history.get(idx)
        if not h or lap_num < 1 or lap_num > len(h["laps"]):
            return None
        l = h["laps"][lap_num - 1]
        if l["lapTimeInMS"] <= 0:
            return None
        return (sector_ms(l["sector1TimeMSPart"], l["sector1TimeMinutes"]),
                sector_ms(l["sector2TimeMSPart"], l["sector2TimeMinutes"]),
                sector_ms(l["sector3TimeMSPart"], l["sector3TimeMinutes"]))

    def pop_events(self, since: float) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["time"] > since]

    # ---------------------------------------------------------------- text output

    def one_line_status(self) -> str:
        """Single line for the --test display."""
        with self.lock:
            if self.packet_format is None:
                return "waiting for telemetry..."
            if self.packet_format != PACKET_FORMAT:
                return (f"game is sending UDP format {self.packet_format} — "
                        f"set 'UDP Format' to 2026 in the game's telemetry settings")
            self._prune_rate()
            s = self.session
            lap = self.player(self.lap)
            tel = self.player(self.telemetry)
            parts = [f"v{self.game_version}", f"{len(self._rate_window)} pkt/s", f"from {self.last_sender}"]
            if s:
                parts.append(f'{TRACKS.get(s["trackId"], "track " + str(s["trackId"]))} · '
                             f'{SESSION_TYPES.get(s["sessionType"], "?")}')
            if lap:
                parts.append(f'lap {lap["currentLapNum"]} · P{lap["carPosition"]} · '
                             f'last {fmt_lap(lap["lastLapTimeInMS"])}')
            if tel:
                parts.append(f'{self.speed_conv(tel["speed"]):.0f} {self.speed_units()} · gear {tel["gear"]}')
            return " | ".join(parts)

    def quick_status(self) -> str:
        """Short spoken summary without the LLM (position, gaps, tyres, fuel)."""
        with self.lock:
            lap = self.player(self.lap)
            st = self.player(self.status)
            dmg = self.player(self.damage)
            if not lap:
                return "No telemetry yet."
            bits = [f'P{lap["carPosition"]}, lap {lap["currentLapNum"]}']
            if self.session and self.is_race():
                bits[-1] += f' of {self.session["totalLaps"]}'
            if lap["lastLapTimeInMS"]:
                bits.append(f'last lap {fmt_lap_spoken(lap["lastLapTimeInMS"])}')
            ahead = sector_ms(lap["deltaToCarInFrontMSPart"], lap["deltaToCarInFrontMinutesPart"])
            if lap["carPosition"] > 1 and ahead:
                bits.append(f"gap ahead {ahead / 1000:.1f}")
            behind = self._gap_behind_ms(lap)
            if behind is not None:
                bits.append(f"behind {behind / 1000:.1f}")
            if st:
                comp = VISUAL_COMPOUND.get(st["visualTyreCompound"], "?")
                bits.append(f'{comp} tyres, {st["tyresAgeLaps"]} laps old')
                if self.is_race():
                    bits.append(f'fuel {st["fuelRemainingLaps"]:+.1f} laps')
                bits.append(f'ERS {st["ersStoreEnergy"] / 4_000_000 * 100:.0f} percent')
            if dmg:
                worst = max(dmg["tyresWear"])
                bits.append(f"worst tyre wear {worst:.0f} percent")
            return ". ".join(bits) + "."

    def _gap_behind_ms(self, my_lap: dict) -> int | None:
        """Gap to the car directly behind, from that car's delta-to-car-in-front."""
        my_pos = my_lap["carPosition"]
        for i, l in enumerate(self.lap):
            if l and i != self.player_idx and l["carPosition"] == my_pos + 1 and l["resultStatus"] in (2, 3):
                return sector_ms(l["deltaToCarInFrontMSPart"], l["deltaToCarInFrontMinutesPart"])
        return None

    def snapshot_text(self) -> str:
        """Compact, LLM-friendly description of everything that matters right now."""
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> str:
        if self.packet_format is None:
            return "NO TELEMETRY RECEIVED YET — the game is not sending data."
        if self.packet_format != PACKET_FORMAT:
            return f"WRONG UDP FORMAT ({self.packet_format}) — game must be set to UDP format 2026."
        stale = self.seconds_since_packet()
        lines: list[str] = []
        if stale > 5:
            lines.append(f"WARNING: no packets for {stale:.0f}s (game paused, in menus, or telemetry off). Data below is stale.")

        s = self.session
        pi = self.player_idx
        lap = self.player(self.lap)
        tel = self.player(self.telemetry)
        tel2 = self.player(self.telemetry2)
        st = self.player(self.status)
        dmg = self.player(self.damage)
        setup = self.player(self.setup)
        race = self.is_race()

        # --- session
        if s:
            track = TRACKS.get(s["trackId"], f'track {s["trackId"]}')
            stype = SESSION_TYPES.get(s["sessionType"], "unknown session")
            line = f"SESSION: {stype} at {track}"
            if race:
                line += f', {s["totalLaps"]} laps'
            else:
                line += f', {s["sessionTimeLeft"] // 60}:{s["sessionTimeLeft"] % 60:02d} left'
            line += (f'. Weather {WEATHER.get(s["weather"], "?")}, track {self.temp_str(s["trackTemperature"])}, '
                     f'air {self.temp_str(s["airTemperature"])}. Safety car: {SAFETY_CAR.get(s["safetyCarStatus"], "?")}.')
            if s["formula"] == 13 or (tel2 and tel2["regulations2026"]):
                line += " Cars: 2026 regulations (active aero + Overtake mode, no DRS)."
            lines.append(line)
            fc = [f for f in s["forecast"] if f["sessionType"] == s["sessionType"] and f["timeOffset"] > 0][:6]
            if fc:
                lines.append("FORECAST: " + ", ".join(
                    f'+{f["timeOffset"]}min {WEATHER.get(f["weather"], "?")} rain {f["rainPercentage"]}%' for f in fc))
            if race and s["pitStopWindowIdealLap"]:
                lines.append(f'PIT STRATEGY (game): ideal pit lap {s["pitStopWindowIdealLap"]}, '
                             f'latest lap {s["pitStopWindowLatestLap"]}, would rejoin P{s["pitStopRejoinPosition"]}.')
            lines.append(f'AI difficulty {s["aiDifficulty"]}. Assists: TC {s["tractionControlAssist"]}, '
                         f'ABS {s["antiLockBrakesAssist"]}, gearbox {s["gearboxAssist"]}, racing line {s["dynamicRacingLine"]}.')

        # --- player
        if lap:
            best = self.best_lap_ms(pi)
            line = (f'DRIVER: P{lap["carPosition"]}, lap {lap["currentLapNum"]}, sector {lap["sector"] + 1}, '
                    f'{DRIVER_STATUS.get(lap["driverStatus"], "?")}{" " + PIT_STATUS[lap["pitStatus"]] if lap["pitStatus"] else ""}. '
                    f'Current lap {fmt_lap(lap["currentLapTimeInMS"])}{" (INVALID)" if lap["currentLapInvalid"] else ""}, '
                    f'last lap {fmt_lap(lap["lastLapTimeInMS"])}, best lap {fmt_lap(best)}.')
            lines.append(line)
            bs = self.best_sectors_ms(pi)
            if all(bs):
                ideal = sum(bs)  # type: ignore[arg-type]
                lines.append(f'BEST SECTORS: S1 {fmt_lap(bs[0])}, S2 {fmt_lap(bs[1])}, S3 {fmt_lap(bs[2])} '
                             f'(ideal lap {fmt_lap(ideal)}).')
            h = self.player_history()
            if h and h["laps"]:
                recent = [l for l in h["laps"] if l["lapTimeInMS"] > 0][-5:]
                if recent:
                    start = len([l for l in h["laps"] if l["lapTimeInMS"] > 0]) - len(recent) + 1
                    lines.append("RECENT LAPS: " + "; ".join(
                        f'L{start + k} {fmt_lap(l["lapTimeInMS"])} '
                        f'({fmt_lap(sector_ms(l["sector1TimeMSPart"], l["sector1TimeMinutes"]))}/'
                        f'{fmt_lap(sector_ms(l["sector2TimeMSPart"], l["sector2TimeMinutes"]))}/'
                        f'{fmt_lap(sector_ms(l["sector3TimeMSPart"], l["sector3TimeMinutes"]))}'
                        f'{"" if l["lapValidBitFlags"] & 1 else " invalid"})'
                        for k, l in enumerate(recent)))
            if race or (s and s["sessionType"] not in (18,)):
                ahead = sector_ms(lap["deltaToCarInFrontMSPart"], lap["deltaToCarInFrontMinutesPart"])
                leader = sector_ms(lap["deltaToRaceLeaderMSPart"], lap["deltaToRaceLeaderMinutesPart"])
                behind = self._gap_behind_ms(lap)
                gaps = []
                if lap["carPosition"] > 1:
                    gaps.append(f'ahead {fmt_gap(ahead)}, to leader {fmt_gap(leader)}')
                if behind is not None:
                    gaps.append(f'behind {fmt_gap(behind)}')
                if gaps:
                    lines.append("GAPS: " + ", ".join(gaps) + ".")
            pen = []
            if lap["penalties"]:
                pen.append(f'{lap["penalties"]}s time penalty')
            if lap["totalWarnings"]:
                pen.append(f'{lap["totalWarnings"]} warnings ({lap["cornerCuttingWarnings"]} corner cutting)')
            if lap["numUnservedDriveThroughPens"]:
                pen.append(f'{lap["numUnservedDriveThroughPens"]} drive-through to serve')
            if lap["numUnservedStopGoPens"]:
                pen.append(f'{lap["numUnservedStopGoPens"]} stop-go to serve')
            if pen:
                lines.append("PENALTIES: " + ", ".join(pen) + ".")
            if race:
                lines.append(f'PIT STOPS: {lap["numPitStops"]}. Started P{lap["gridPosition"]}.')

        if st:
            comp = f'{VISUAL_COMPOUND.get(st["visualTyreCompound"], "?")} ({ACTUAL_COMPOUND.get(st["actualTyreCompound"], "?")})'
            line = f'TYRES: {comp}, {st["tyresAgeLaps"]} laps old'
            if dmg:
                w = dmg["tyresWear"]
                line += f', wear FL {w[2]:.0f}% FR {w[3]:.0f}% RL {w[0]:.0f}% RR {w[1]:.0f}%'
            if tel:
                t = tel["tyresInnerTemperature"]
                ts = tel["tyresSurfaceTemperature"]
                line += (f'. Carcass temps FL {t[2]} FR {t[3]} RL {t[0]} RR {t[1]} C, '
                         f'surface FL {ts[2]} FR {ts[3]} RL {ts[0]} RR {ts[1]} C')
            lines.append(line + ".")
            ers_pct = st["ersStoreEnergy"] / 4_000_000 * 100
            line = (f'ENERGY: fuel {st["fuelInTank"]:.1f} kg, {st["fuelRemainingLaps"]:+.1f} laps vs race end, '
                    f'mix {FUEL_MIX.get(st["fuelMix"], "?")}. ERS {ers_pct:.0f}%, deploy mode {ERS_MODE.get(st["ersDeployMode"], "?")}, '
                    f'deployed this lap {st["ersDeployedThisLap"] / 1e6:.2f} MJ, harvested {(st["ersHarvestedThisLapMGUK"] + st["ersHarvestedThisLapMGUH"]) / 1e6:.2f} MJ.')
            lines.append(line)
            line = f'CAR: brake bias {st["frontBrakeBias"]}% front, flag {FLAGS.get(st["vehicleFiaFlags"], "?")}'
            if tel2 and tel2["regulations2026"]:
                line += (f', active aero {"available" if tel2["activeAeroAvailable"] else "not available"}'
                         f' ({"straight" if tel2["activeAeroMode"] else "corner"} mode)'
                         f', Overtake mode {"ACTIVE" if tel2["overtakeActive"] else ("available" if tel2["overtakeAvailable"] else "not available")}')
            else:
                line += f', DRS {"allowed" if st["drsAllowed"] else "not allowed"}'
            if tel:
                line += (f'. Speed {self.speed_conv(tel["speed"]):.0f} {self.speed_units()}, gear {tel["gear"]}, '
                         f'{tel["engineRPM"]} rpm, engine {tel["engineTemperature"]} C, '
                         f'brakes FL {tel["brakesTemperature"][2]} FR {tel["brakesTemperature"][3]} '
                         f'RL {tel["brakesTemperature"][0]} RR {tel["brakesTemperature"][1]} C')
            lines.append(line + ".")

        if dmg:
            items = []
            for key, label in (("frontLeftWingDamage", "front wing L"), ("frontRightWingDamage", "front wing R"),
                               ("rearWingDamage", "rear wing"), ("floorDamage", "floor"),
                               ("diffuserDamage", "diffuser"), ("sidepodDamage", "sidepod"),
                               ("gearBoxDamage", "gearbox"), ("engineDamage", "engine")):
                if dmg[key]:
                    items.append(f"{label} {dmg[key]}%")
            if dmg["drsFault"]:
                items.append("DRS FAULT")
            if dmg["ersFault"]:
                items.append("ERS FAULT")
            lines.append("DAMAGE: " + (", ".join(items) if items else "none") + ".")

        if setup:
            lines.append(f'SETUP: wings {setup["frontWing"]}/{setup["rearWing"]}, diff {setup["onThrottle"]}/{setup["offThrottle"]}%, '
                         f'camber {setup["frontCamber"]:.1f}/{setup["rearCamber"]:.1f}, toe {setup["frontToe"]:.2f}/{setup["rearToe"]:.2f}, '
                         f'susp {setup["frontSuspension"]}/{setup["rearSuspension"]}, ARB {setup["frontAntiRollBar"]}/{setup["rearAntiRollBar"]}, '
                         f'ride height {setup["frontSuspensionHeight"]}/{setup["rearSuspensionHeight"]}, '
                         f'brake pressure {setup["brakePressure"]}%, bias {setup["brakeBias"]}%, engine braking {setup["engineBraking"]}%, '
                         f'pressures F {setup["frontLeftTyrePressure"]:.1f}/{setup["frontRightTyrePressure"]:.1f} '
                         f'R {setup["rearLeftTyrePressure"]:.1f}/{setup["rearRightTyrePressure"]:.1f} psi, fuel load {setup["fuelLoad"]:.1f}.')

        if self.tyre_sets:
            avail = [ts for ts in self.tyre_sets["sets"] if ts["available"] and not ts["fitted"]]
            if avail:
                lines.append("SPARE TYRE SETS: " + ", ".join(
                    f'{VISUAL_COMPOUND.get(ts["visualTyreCompound"], "?")} {ts["wear"]}% worn'
                    f'{" " + str(ts["lapDeltaTime"] / 1000) + "s/lap vs fitted" if ts["lapDeltaTime"] else ""}'
                    for ts in avail[:8]) + ".")

        if self.time_trial:
            tt = self.time_trial
            def tt_line(label: str, d: dict) -> str:
                if not d["valid"] or not d["lapTimeInMS"]:
                    return f"{label} none"
                return (f'{label} {fmt_lap(d["lapTimeInMS"])} ({fmt_lap(d["sector1TimeInMS"])}/'
                        f'{fmt_lap(d["sector2TimeInMS"])}/{fmt_lap(d["sector3TimeInMS"])})')
            lines.append("TIME TRIAL: " + ", ".join([tt_line("session best", tt["sessionBest"]),
                                                     tt_line("personal best", tt["personalBest"]),
                                                     tt_line("rival", tt["rival"])]) + ".")

        # --- other cars
        if s and not self.is_time_trial():
            rows = []
            for i, l in enumerate(self.lap):
                if not l or l["resultStatus"] not in (2, 3) or l["carPosition"] == 0:
                    continue
                rows.append((l["carPosition"], i, l))
            rows.sort()
            if rows:
                my_pos = lap["carPosition"] if lap else 0
                keep = [r for r in rows if r[0] <= 3 or abs(r[0] - my_pos) <= 3]
                out = []
                for pos, i, l in keep:
                    cs = self.status[i]
                    tag = "YOU" if i == pi else self.car_name(i)
                    item = f'P{pos} {tag}: last {fmt_lap(l["lastLapTimeInMS"])}, best {fmt_lap(self.best_lap_ms(i))}'
                    if race:
                        lead = sector_ms(l["deltaToRaceLeaderMSPart"], l["deltaToRaceLeaderMinutesPart"])
                        item += f', +{lead / 1000:.1f}s, {l["numPitStops"]} stops'
                    if cs:
                        item += f', {VISUAL_COMPOUND.get(cs["visualTyreCompound"], "?")} {cs["tyresAgeLaps"]}L'
                    if l["pitStatus"]:
                        item += ", IN PITS"
                    out.append(item)
                lines.append("FIELD (top 3 + cars near you): " + " | ".join(out))

        # --- recent events
        recent = [e for e in self.events if time.time() - e["time"] < 120][-8:]
        if recent:
            lines.append("RECENT EVENTS (last 2 min): " + "; ".join(self.describe_event(e) for e in recent))

        return "\n".join(lines)

    def describe_event(self, e: dict) -> str:
        c = e["code"]
        who = lambda k: "YOU" if e.get(k) == self.player_idx else self.car_name(e.get(k))  # noqa: E731
        if c == "SSTA":
            return "session started"
        if c == "SEND":
            return "session ended"
        if c == "FTLP":
            return f'fastest lap {fmt_lap(e["lapTime"] * 1000)} by {who("vehicleIdx")}'
        if c == "RTMT":
            return f'{who("vehicleIdx")} retired'
        if c == "DRSE":
            return "DRS enabled"
        if c == "DRSD":
            return "DRS disabled"
        if c == "TMPT":
            return "team mate in pits"
        if c == "CHQF":
            return "chequered flag"
        if c == "RCWN":
            return f'race winner {who("vehicleIdx")}'
        if c == "PENA":
            return (f'{who("vehicleIdx")}: {PENALTY_TYPES.get(e["penaltyType"], "penalty")} '
                    f'for {INFRINGEMENTS.get(e["infringementType"], "infringement")}'
                    f'{" " + str(e["timeSeconds"]) + "s" if e["timeSeconds"] else ""}')
        if c == "SPTP":
            return f'speed trap {who("vehicleIdx")} {self.speed_conv(e["speed"]):.0f} {self.speed_units()}'
        if c == "STLG":
            return f'start lights {e["numLights"]}'
        if c == "LGOT":
            return "lights out"
        if c == "DTSV":
            return f'{who("vehicleIdx")} served drive through'
        if c == "SGSV":
            return f'{who("vehicleIdx")} served stop-go'
        if c == "FLBK":
            return "flashback used"
        if c == "RDFL":
            return "RED FLAG"
        if c == "OVTK":
            return f'{who("overtakingIdx")} overtook {who("overtakenIdx")}'
        if c == "SCAR":
            kind = {0: "no safety car", 1: "safety car", 2: "virtual safety car", 3: "formation lap safety car"}.get(e["safetyCarType"], "safety car")
            what = {0: "deployed", 1: "returning", 2: "returned", 3: "race resumed"}.get(e["eventType"], "")
            return f"{kind} {what}"
        if c == "COLL":
            sev = {0: "light", 1: "medium", 2: "heavy"}.get(e["severity"], "")
            return f'{sev} collision between {who("vehicle1Idx")} and {who("vehicle2Idx")}'
        return c


# ---------------------------------------------------------------------------
# UDP listener thread
# ---------------------------------------------------------------------------

class TelemetryListener(threading.Thread):
    """Receives game packets on a UDP port and feeds them into a RaceState."""

    def __init__(self, state: RaceState, port: int = DEFAULT_PORT):
        super().__init__(daemon=True, name="f1-udp-listener")
        self.state = state
        self.port = port
        self.error: str | None = None
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", self.port))
        except OSError as e:
            self.error = f"could not open UDP port {self.port}: {e}"
            return
        sock.settimeout(0.5)
        while not self._stop_event.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as e:
                self.error = str(e)
                break
            self.state.ingest(data, addr[0])
        sock.close()


# ---------------------------------------------------------------------------
# Self-test: python f1_udp.py
# ---------------------------------------------------------------------------

def _self_test() -> None:
    """Checks every layout adds up to the packet sizes in the spec."""
    assert HEADER.size == 29
    checks = {
        PKT_SESSION: 29 + SESSION_A.size + 21 * MARSHAL_ZONE.size + SESSION_B.size + 64 * WEATHER_SAMPLE.size
        + SESSION_C.size + 8 * AERO_ZONE.size + SESSION_D.size + 8 * AERO_ZONE.size + SESSION_E.size
        + 4 * AERO_ZONE.size + SESSION_F.size,
        PKT_LAP: 29 + MAX_CARS * LAP_DATA.size + LAP_TAIL.size,
        PKT_PARTICIPANTS: 29 + 1 + MAX_CARS * PARTICIPANT.size,
        PKT_SETUPS: 29 + MAX_CARS * CAR_SETUP.size + 4,
        PKT_TELEMETRY: 29 + MAX_CARS * CAR_TELEMETRY.size + TELEMETRY_TAIL.size,
        PKT_STATUS: 29 + MAX_CARS * CAR_STATUS.size,
        PKT_DAMAGE: 29 + MAX_CARS * CAR_DAMAGE.size,
        PKT_HISTORY: 29 + HISTORY_HEAD.size + 100 * LAP_HISTORY.size + 8 * TYRE_STINT.size,
        PKT_TYRE_SETS: 29 + 1 + 20 * TYRE_SET.size + 1,
        PKT_TIME_TRIAL: 29 + 3 * TIME_TRIAL_SET.size,
        PKT_TELEMETRY2: 29 + MAX_CARS * CAR_TELEMETRY2.size,
        PKT_FINAL: 29 + 1 + MAX_CARS * FINAL_CLASSIFICATION.size,
    }
    for pid, size in checks.items():
        assert size == EXPECTED_SIZES[pid], f"packet {pid}: computed {size}, spec {EXPECTED_SIZES[pid]}"
    print("all packet layouts match the F1 26 spec sizes")


if __name__ == "__main__":
    _self_test()
