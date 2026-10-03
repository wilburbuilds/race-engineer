"""Distance traces for the field. All methods run under RaceState.lock.

Keep six recent laps per driver at 10 Hz. Never manufacture opponent samples
from leaderboard times or Time Trial ghosts.
"""
import copy
import math

from f1_udp import PKT_LAP, PKT_TELEMETRY


class OpponentTraces:
    COLUMNS = ['distance_m', 'lap_time_ms', 'speed_kph', 'throttle', 'brake', 'gear', 'session_time_s']
    MAX_LAPS = 6
    MAX_ROWS = 6000

    def __init__(self, state):
        self.state = state
        self.uid = None
        self.drivers = {}
        self.lap_packet_time = None
        self.revision = 0

    def ingest(self, pid):
        st = self.state
        if self.uid != st.session_uid:
            self.uid = st.session_uid
            self.drivers = {}
            self.lap_packet_time = None
            self.revision = 0
        if not st.session or st.is_time_trial():
            return
        now = st.session_time
        if pid == PKT_LAP:
            self.lap_packet_time = now
            for i, lap in enumerate(st.lap):
                driver = self.drivers.get(i)
                if not driver or not lap:
                    continue
                current = lap['currentLapNum']
                for number, record in list(driver['laps'].items()):
                    if number > current:
                        del driver['laps'][number]  # flashback across the line
                    elif number == current:
                        if record['rows'] and lap['currentLapTimeInMS'] < record['rows'][-1][1] - 1000:
                            del driver['laps'][number]
                            continue
                        record['invalid'] |= bool(lap['currentLapInvalid'])
                        record['pit'] |= bool(lap['pitStatus'])
                    elif not record['finished']:
                        record['finished'] = True
                        record['time_ms'] = lap['lastLapTimeInMS'] if number == current - 1 else None
                        rows = record['rows']
                        record['full'] = bool(rows and rows[0][0] < 100 and rows[-1][0] > st.session['trackLength'] - 100 and not record['gaps'])
                self.revision += 1
            return
        if pid != PKT_TELEMETRY or st.session.get('gamePaused'):
            return
        if self.lap_packet_time is None or not 0 <= now - self.lap_packet_time <= .25:
            return  # do not pair new telemetry with stale positions
        for i, (lap, tel) in enumerate(zip(st.lap, st.telemetry)):
            if not lap or not tel or lap['resultStatus'] not in (2, 3) or lap['driverStatus'] == 0:
                continue
            distance = lap['lapDistance']
            number = lap['currentLapNum']
            values = [distance, lap['currentLapTimeInMS'], tel['speed'], tel['throttle'], tel['brake'], tel['gear'], now]
            if number < 1 or distance < 0 or distance > st.session['trackLength'] + 25 or not all(math.isfinite(v) for v in values):
                continue
            participant = st.participants[i]
            # Wait for identity before recording a slot, to avoid mixing driver changes.
            if not participant:
                continue
            identity = tuple(participant.get(k) for k in ('networkId', 'raceNumber', 'name', 'aiControlled'))
            driver = self.drivers.get(i)
            if not driver or driver['_identity'] != identity:
                driver = {'id': i, 'name': st.car_name(i), 'you': i == st.player_idx,
                          '_identity': identity, 'laps': {}}
                self.drivers[i] = driver
            record = driver['laps'].setdefault(number, {'lap': number, 'finished': False, 'full': False,
                'invalid': False, 'pit': False, 'gaps': False, 'time_ms': None, 'rows': []})
            rows = record['rows']
            record['invalid'] |= bool(lap['currentLapInvalid'])
            record['pit'] |= bool(lap['pitStatus'])
            if rows:
                previous = rows[-1]
                if now < previous[6] or values[1] < previous[1] - 1000:
                    rows.clear()
                    record.update(finished=False, full=False, time_ms=None, gaps=True)
                elif distance < previous[0] - 2:
                    record['gaps'] = True
                    continue  # reversing: do not give the charts non-monotone distances
                elif distance <= previous[0] or now - previous[6] < .095:
                    continue
                elif now - previous[6] > .5 or distance - previous[0] > 100:
                    record['gaps'] = True
            if len(rows) >= self.MAX_ROWS:
                record['gaps'] = True
                continue
            rows.append([round(distance, 2), values[1], values[2], round(values[3], 3), round(values[4], 3), values[5], round(now, 3)])
            clean = [r for r in driver['laps'].values() if r['finished'] and r['full'] and not r['invalid'] and not r['pit'] and r['time_ms']]
            best = min(clean, key=lambda r:r['time_ms'])['lap'] if clean else None
            keep = set(sorted(driver['laps'])[-self.MAX_LAPS:])
            if best is not None: keep.add(best)
            for old in list(driver['laps']):
                if old not in keep: del driver['laps'][old]
            self.revision += 1

    def snapshot(self, include_rows=False):
        out = {'session_id': str(self.uid), 'revision': self.revision, 'columns': self.COLUMNS,
               'track_length_m': (self.state.session or {}).get('trackLength'), 'drivers': []}
        for driver in self.drivers.values():
            item = {k: v for k, v in driver.items() if k not in ('_identity', 'laps')}
            item['laps'] = []
            for record in driver['laps'].values():
                lap = {k: v for k, v in record.items() if k != 'rows'}
                lap['samples'] = len(record['rows'])
                if include_rows:
                    lap['rows'] = copy.deepcopy(record['rows'])
                item['laps'].append(lap)
            out['drivers'].append(item)
        return out

    def trace(self, driver_id, lap):
        record = self.drivers.get(driver_id, {}).get('laps', {}).get(lap)
        return {'session_id': str(self.uid), 'columns': self.COLUMNS,
                'trace': copy.deepcopy(record)}
