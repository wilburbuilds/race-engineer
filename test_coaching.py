import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from coaching import analyse, assess_laps, evaluate_trial, extract_response, comparable, atomic_json, CoachingService


def fixture():
    info={'track':'Test','session_type':'Short Practice','track_length_m':1000,
          'setup':{'frontWing':25,'rearWing':25,'onThrottle':70,'brakePressure':100},
          'setup_history':[{'revision':1,'session_time_s':0,'setup':{'frontWing':25,'rearWing':25,'onThrottle':70,'brakePressure':100}}]}
    laps=[];traces={}
    for n,ms in enumerate([10000,10300,10400],1):
        laps.append({'lap':n,'valid':True,'time_ms':ms,'compound':'Soft','fuel_kg':10,'tyre_age_laps':n,
                     'setup_revision':1,'front_wing_damage':0})
        traces[n]=[{'distance_m':d,'session_time_s':n*20+d/100,'lap_time_ms':d*ms/1000,
                    'speed_kph':100,'brake':.5 if 100<=d<=300 else 0,'throttle':.3 if d<400 else 1}
                   for d in range(0,1001,10)]
    return info,laps,traces

class CoachingTest(unittest.TestCase):
    def test_clean_baseline_and_small_feedback_test(self):
        info,laps,traces=fixture();r=analyse(info,laps,traces,{'symptom':'wont_turn'})
        self.assertEqual(r['eligible_count'],3);self.assertEqual(r['proposal']['proposed'],26)
        self.assertEqual(r['proposal']['baseline_laps'],[1,2,3])
        self.assertNotIn('proposal',r['debrief'])
    def test_invalid_pit_damage_and_gap_excluded(self):
        info,laps,traces=fixture()
        laps[0]['pit_lap']=True;laps[1]['front_wing_damage']=5;traces[3][50]['session_time_s']+=5
        result=assess_laps(info,laps,traces)
        self.assertTrue(all(not l['eligible'] for l in result))
        self.assertIn('pit lap',result[0]['reasons']);self.assertIn('front wing damage',result[1]['reasons'])
    def test_unusually_slow_valid_lap_is_not_a_baseline(self):
        info,laps,traces=fixture();laps[2]['time_ms']=20000
        self.assertFalse(assess_laps(info,laps,traces)[2]['eligible'])
    def test_comparisons_do_not_mix_fuel_compound_or_setup(self):
        info,laps,traces=fixture();a=assess_laps(info,laps,traces)[0]
        for field,value in [('fuel_kg',20),('compound','Hard'),('setup_revision',2)]:
            self.assertFalse(comparable(a,dict(a,**{field:value})))
    def test_legacy_reports_explain_missing_history_without_setup_proposals(self):
        info,laps,traces=fixture();info.pop('setup_history')
        for l in laps:l.pop('setup_revision')
        r=analyse(info,laps,traces,{'symptom':'wont_turn'})
        self.assertIsNone(r['proposal']);self.assertIn('unverified',r['confidence'])
        self.assertTrue(r['uncertainties'])
    def test_trial_detects_application_and_evaluates_medians(self):
        info,laps,traces=fixture();base=assess_laps(info,laps,traces)
        trial={'key':'frontWing','proposed':26,'baseline_setup':info['setup'],'baseline_laps':[1,2,3],'created_session_time_s':1}
        self.assertEqual(evaluate_trial(trial,base,info['setup_history'])['status'],'waiting')
        rev={'revision':2,'session_time_s':100,'setup':dict(info['setup'],frontWing=26)}
        after=[dict(base[i],lap=i+4,setup_revision=2,time_ms=9700+i*10) for i in range(3)]
        r=evaluate_trial(trial,base+after,[rev]);self.assertEqual(r['status'],'evaluated');self.assertLess(r['delta_ms'],0)
        rev['setup']['rearWing']=24
        self.assertEqual(evaluate_trial(trial,base+after,[rev])['status'],'waiting')
    def test_structured_output_validation(self):
        answer={'summary':'a','driving_tip':'b','next_practice':'c','uncertainty':'d'}
        self.assertEqual(extract_response({'result':__import__('json').dumps(answer)}),answer)
        self.assertEqual(extract_response({'structuredOutput':answer,'text':'ignored'}),answer)
        self.assertEqual(extract_response({'text':__import__('json').dumps(answer),'stopReason':'end_turn'}),answer)
        self.assertIsNone(extract_response({'summary':42}))
    def test_recording_result_uncertainty(self):
        info,laps,traces=fixture();info['session_type']='Race'
        self.assertTrue(any('classification' in s for s in analyse(info,laps,traces)['uncertainties']))
    def test_feedback_validation_and_trial_persistence(self):
        with tempfile.TemporaryDirectory() as tmp, patch('coaching.threading.Thread'):
            service=CoachingService();folder=Path(tmp)
            with self.assertRaises(ValueError):service.update(folder,'wrong')
            service.update(folder,'rear_slides')
            self.assertEqual(__import__('json').loads((folder/'coaching-state.json').read_text())['symptom'],'rear_slides')
            with self.assertRaises(ValueError):service.update(folder,start_trial=True)

if __name__=='__main__':unittest.main()

class CoachingWorkerTest(unittest.TestCase):
    def test_background_report_and_shutdown_debrief(self):
        import csv,json,time
        from coaching import markdown
        info,laps,traces=fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'RACE_ENGINEER_GROK':'off'}):
            folder=Path(tmp);atomic_json(folder/'session.json',info)
            with (folder/'laps.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(laps[0]));writer.writeheader();writer.writerows(laps)
            columns=['lap']+list(traces[1][0])
            with (folder/'trace.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
                for lap,rows in traces.items():writer.writerows(dict(row,lap=lap) for row in rows)
            service=CoachingService();service.request(folder)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if (folder/'coaching.json').exists() and service.status.get(str(folder))=='idle':break
                time.sleep(.02)
            report=json.loads((folder/'coaching.json').read_text())
            self.assertEqual(report['eligible_count'],3)
            self.assertIn('Next practice',(folder/'debrief.md').read_text())
            service.finish(folder)
            self.assertEqual(json.loads((folder/'coaching.json').read_text())['lap_count'],3)

class RecordingSemanticsTest(unittest.TestCase):
    def test_weather_assists_and_temperature_are_matched(self):
        info,laps,traces=fixture();a=assess_laps(info,laps,traces)[0]
        for change in [{'weather':'rain'},{'assists_key':'changed'},{'track_temp_c':40}]:
            base=dict(a,weather='clear',assists_key='off',track_temp_c=30)
            self.assertFalse(comparable(base,dict(base,**change)))
    def test_completed_race_uses_earlier_comparable_group(self):
        info,laps,traces=fixture();info['session_type']='Race'
        laps.append(dict(laps[-1],lap=4,tyre_age_laps=10,fuel_kg=1));traces[4]=traces[3]
        r=analyse(info,laps,traces)
        self.assertEqual(r['comparable_laps'],[1,2,3])
    def test_last_on_track_snapshot_survives_line_and_new_tyres(self):
        from engineer import SessionLogger
        from f1_udp import RaceState,CAR_STATUS,CAR_DAMAGE
        with tempfile.TemporaryDirectory() as temp, patch('engineer.threading.Thread'):
            st=RaceState();st.player_idx=0
            st.status[0]=dict(CAR_STATUS.unpack(bytes(CAR_STATUS.size)),fuelInTank=10,visualTyreCompound=16)
            st.damage[0]=CAR_DAMAGE.unpack(bytes(CAR_DAMAGE.size))
            logger=SessionLogger(st,Path(temp))
            logger._snapshot_lap(1,{'currentLapNum':1,'lastLapTimeInMS':0,'numPitStops':0,'carPosition':2})
            st.status[0]['fuelInTank']=9
            logger._snapshot_lap(1,{'currentLapNum':2,'lastLapTimeInMS':10000,'numPitStops':1,'carPosition':3})
            self.assertEqual(logger.lap_snapshots[1]['fuel_kg'],10)
            self.assertEqual(logger.lap_snapshots[1]['position'],2)
