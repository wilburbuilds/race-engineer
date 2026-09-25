import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from opponent_traces import OpponentTraces
from engineer import DashboardServer
from f1_udp import RaceState, LAP_DATA, CAR_TELEMETRY, PARTICIPANT, PKT_LAP, PKT_TELEMETRY

class FieldTraceTest(unittest.TestCase):
    def setUp(self):
        self.st = RaceState()
        self.st.session_uid = 10
        self.st.player_idx = 0
        self.st.session = {'sessionType': 10, 'trackLength': 1000, 'gamePaused': 0}
        for i in (0,1):
            self.st.participants[i] = dict(PARTICIPANT.unpack(bytes(PARTICIPANT.size)), name='Driver '+str(i))
            self.st.lap[i] = dict(LAP_DATA.unpack(bytes(LAP_DATA.size)), currentLapNum=1, driverStatus=1, resultStatus=2)
            self.st.telemetry[i] = dict(CAR_TELEMETRY.unpack(bytes(CAR_TELEMETRY.size)), speed=200, throttle=.75, brake=0)
        self.traces = OpponentTraces(self.st)
    def sample(self, t, distance, lap=1):
        self.st.session_time = t
        for l in self.st.lap[:2]:
            l.update(lapDistance=distance, currentLapNum=lap, currentLapTimeInMS=int((t % 100)*1000))
        self.traces.ingest(PKT_LAP)
        self.traces.ingest(PKT_TELEMETRY)
    def test_distance_rows_and_lap_completion(self):
        for i in range(100): self.sample(i*.1, i*10)
        self.st.lap[0]['lastLapTimeInMS']=10000
        self.sample(100, 0, 2)
        lap=self.traces.trace(0,1)['trace']
        self.assertTrue(lap['finished']); self.assertTrue(lap['full'])
        self.assertEqual(lap['time_ms'],10000)
        self.assertEqual(lap['rows'][0][2:5],[200,.75,0])
        self.assertEqual(len(self.traces.snapshot()['drivers']),2)
    def test_join_midlap_gaps_and_no_extrapolation_source(self):
        self.sample(5,500); self.sample(6,600); self.sample(100,0,2)
        lap=self.traces.trace(1,1)['trace']
        self.assertFalse(lap['full']); self.assertTrue(lap['gaps'])
        self.assertEqual(lap['rows'][0][0],500)
    def test_pause_stale_packet_and_missing_driver(self):
        self.sample(1,100)
        self.st.session['gamePaused']=1; self.sample(2,200)
        self.assertEqual(self.traces.snapshot()['drivers'][0]['laps'][0]['samples'],1)
        self.st.session['gamePaused']=0; self.st.session_time=4
        self.traces.ingest(PKT_TELEMETRY)
        self.assertEqual(self.traces.snapshot()['drivers'][0]['laps'][0]['samples'],1)
        self.st.participants[1]=None; self.sample(5,300)
        self.assertEqual(self.traces.snapshot()['drivers'][1]['laps'][0]['samples'],1)
    def test_rewind_session_reset_and_bounds(self):
        self.sample(10,500); self.sample(5,250)
        self.assertEqual(self.traces.trace(0,1)['trace']['rows'][0][0],250)
        for lap in range(2,10): self.sample(lap*100,0,lap)
        self.assertEqual(len(self.traces.snapshot()['drivers'][0]['laps']),6)
        self.st.session_uid=11;self.sample(1,10)
        self.assertEqual(len(self.traces.snapshot()['drivers'][0]['laps']),1)
        self.assertEqual(self.traces.snapshot()['session_id'],'11')
    def test_identity_swap_and_time_trial(self):
        self.sample(1,100);self.st.participants[1]['name']='Replacement';self.sample(2,200)
        self.assertEqual(len(self.traces.trace(1,1)['trace']['rows']),1)
        self.st.session_uid=12;self.st.session['sessionType']=18;self.sample(1,0)
        self.assertEqual(self.traces.snapshot()['drivers'],[])
    def test_saved_catalog_and_trace(self):
        self.sample(1,100)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'test';folder.mkdir()
            (folder/'opponents.json').write_text(json.dumps(self.traces.snapshot(True)))
            server=DashboardServer.__new__(DashboardServer)
            server.logger=SimpleNamespace(root=Path(temp),opponents=self.traces);server.state=self.st
            self.assertNotIn('rows',server.opponents_json('test')['drivers'][0]['laps'][0])
            self.assertEqual(server.opponents_json('test',1,1)['trace']['rows'][0][0],100)
            self.assertIn('error',server.opponents_json('../test'))
            (folder/'opponents.json').unlink()
            self.assertIn('older recording',server.opponents_json('test')['error'])

if __name__=='__main__': unittest.main()
