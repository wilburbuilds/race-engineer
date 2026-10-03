import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from setup_baseline import LABELS, context, parse_setup, lookup, SetupBaselineService
from coaching import analyse,setup_signals,atomic_json
from test_coaching import fixture

CTX={'track':'Melbourne','slug':'australia','year':26,'conditions':'dry','goal':'qualifying','assists':{'traction_control':0,'abs':0}}
URL='https://www.f1laps.com/f1-26/setups/australia/11111111-1111-1111-1111-111111111111/'
VALUES={'frontWing':27,'rearWing':24,'onThrottle':80,'offThrottle':40,'frontCamber':-3.5,'rearCamber':-2,'frontToe':.02,'rearToe':.12,
        'frontSuspension':41,'rearSuspension':3,'frontAntiRollBar':11,'rearAntiRollBar':17,'frontSuspensionHeight':23,'rearSuspensionHeight':49,
        'brakePressure':100,'brakeBias':58,'frontRightTyrePressure':23.3,'frontLeftTyrePressure':23.3,'rearRightTyrePressure':21.9,'rearLeftTyrePressure':21.9}
def page(values=None,session='Qualifying',conditions='Dry'):
    fields={'Session':session,'Conditions':conditions,'Traction Control':'Off','Anti-Lock Brakes':'Off','Team':'Red Bull','Date':'October 1, 2026','Steering':'Wheel'}
    fields.update({LABELS[k]:v for k,v in (values if values is not None else VALUES).items()})
    return '<h1>F1 26 Australia Setup (Dry)</h1>'+''.join(f'<dt>{k}</dt><dd><div></div></dd><dd>{v}</dd>' for k,v in fields.items())

class SourceTest(unittest.TestCase):
    def test_context_requires_known_regulations_weather_and_layout(self):
        self.assertIsNone(context({'track':'Melbourne','weather':'clear'})[0])
        self.assertIsNone(context({'track':'Silverstone (Reverse)','regulations_2026':True,'weather':'clear'})[0])
        self.assertIsNone(context({'track':'Melbourne','regulations_2026':True,'weather':'clear','formula':1})[0])
        self.assertEqual(context({'track':'Melbourne','regulations_2026':True,'weather':'heavy rain'})[0]['conditions'],'wet')
    def test_menu_values_are_extracted_from_source_not_filled_in(self):
        r=parse_setup(page(),URL,CTX)
        self.assertEqual(r['values'],VALUES);self.assertNotIn('engineBraking',r['values']);self.assertTrue(r['missing'])
    def test_wrong_game_track_weather_and_invalid_values_rejected(self):
        for html,url in [(page().replace('F1 26','F1 25'),URL),(page(),URL.replace('australia','singapore')),(page(conditions='Wet'),URL),(page(dict(VALUES,frontWing=99)),URL),(page({k:v for k,v in VALUES.items() if k!='rearWing'}),URL)]:
            with self.assertRaises(ValueError):parse_setup(html,url,CTX)
    def test_lookup_prefers_requested_session_with_assist_filters(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            if '?' in url:return f'<table><tr><td><a href="{URL.removeprefix("https://www.f1laps.com")}">1</a></td></tr></table>'
            return page()
        r=lookup(CTX,fetch)
        self.assertEqual(r['url'],URL);self.assertFalse(r['session_fallback']);self.assertEqual(r['mismatches'],[])
        self.assertIn('a_abs=0',calls[0]);self.assertIn('session=4',calls[0])
    def test_cached_setup_returns_immediately_and_current_values_are_dynamic(self):
        with tempfile.TemporaryDirectory() as temp,patch('setup_baseline.threading.Thread'):
            service=SetupBaselineService(temp)
            info={'track':'Melbourne','weather':'clear','regulations_2026':True,'setup':{'frontWing':10}}
            result=service.get(info);self.assertEqual(result['status'],'searching');self.assertEqual(len(service.pending),1)
            service.get(info);self.assertEqual(len(service.pending),1)
            key=next(iter(service.pending));cached=parse_setup(page(),URL,result['context']);service.results[key]=cached;service.pending.clear()
            result=service.get(info);self.assertEqual(result['status'],'ready');self.assertEqual(result['menu'][0]['current'],10)
            result=service.get(dict(info,setup={'frontWing':27}));self.assertEqual(result['menu'][0]['current'],27)

class SourceWorkerTest(unittest.TestCase):
    def test_offline_failure_preserves_expired_cached_values(self):
        with tempfile.TemporaryDirectory() as temp,patch('setup_baseline.lookup',side_effect=OSError('offline')):
            info={'track':'Melbourne','weather':'clear','regulations_2026':True,'assists':{'traction_control':0,'abs':0}}
            import hashlib
            key=hashlib.sha256(json.dumps(CTX,sort_keys=True).encode()).hexdigest()
            cached=parse_setup(page(),URL,CTX);cached['fetched_at']=time.time()-8*86400
            atomic_json(Path(temp)/(key+'.json'),cached)
            service=SetupBaselineService(temp);service.get(info)
            deadline=time.monotonic()+2
            while service.pending and time.monotonic()<deadline:time.sleep(.01)
            result=service.get(info)
            self.assertEqual(result['status'],'ready');self.assertEqual(result['values'],VALUES)
            self.assertTrue(result['stale']);self.assertIn('unavailable',result['message'])

class AutomaticReviewTest(unittest.TestCase):
    def steering_fixture(self):
        info,laps,traces=fixture()
        laps[1]['time_ms']=10600; laps[2]['time_ms']=10700
        for lap,rows in traces.items():
            for r in rows:
                r['lap_time_ms']=r['distance_m']*laps[lap-1]['time_ms']/1000
                r['steer']=.2 if lap==1 else .4
                r['brake']=0
                r['speed_kph']=80 if lap==1 else 70
        return info,laps,traces
    def test_three_laps_can_propose_without_symptom(self):
        info,laps,traces=self.steering_fixture();r=analyse(info,laps,traces)
        self.assertEqual(r['proposal']['trigger'],'telemetry');self.assertEqual(r['proposal']['proposed'],26)
        self.assertEqual(r['proposal']['evidence']['supporting_laps'],[2,3]);self.assertIn('does not establish',r['proposal']['confidence'])
    def test_two_laps_or_unverified_setup_cannot_trigger_experiment(self):
        info,laps,traces=self.steering_fixture();self.assertIsNone(analyse(info,laps[:2],traces)['proposal'])
        info.pop('setup_history');self.assertIsNone(analyse(info,laps,traces)['proposal'])
    def test_equal_inputs_do_not_invent_setup_faults(self):
        info,laps,traces=fixture();r=analyse(info,laps,traces)
        self.assertIsNone(r['proposal']);self.assertTrue(r['setup_review']['ready'])
    def test_repeated_throttle_corrections_are_not_claimed_as_wheelspin(self):
        info,laps,traces=fixture()
        for lap in (2,3):
            for i,r in enumerate(traces[lap]):
                r['brake']=0;r['steer']=.3
                if 50<=i<=52:r['throttle']=.9
                elif 53<=i<=55:r['throttle']=.3
        r=analyse(info,laps,traces);self.assertEqual(r['proposal']['key'],'onThrottle');self.assertEqual(r['proposal']['proposed'],65)
        self.assertIn('does not measure wheelspin',r['proposal']['confidence'])
    def test_feedback_takes_priority_over_inferred_cue(self):
        info,laps,traces=self.steering_fixture();self.assertEqual(analyse(info,laps,traces,{'symptom':'locks_up'})['proposal']['key'],'brakePressure')
    def test_new_setup_requires_a_new_three_lap_baseline(self):
        info,laps,traces=self.steering_fixture()
        info['setup_history'].append({'revision':2,'session_time_s':100,'setup':dict(info['setup'],frontWing=26)})
        info['setup']['frontWing']=26
        r=analyse(info,laps,traces);self.assertIsNone(r['proposal']);self.assertEqual(r['setup_review']['count'],0)
    def test_active_trial_keeps_review_from_proposing_another_change(self):
        info,laps,traces=self.steering_fixture();trial={'key':'frontWing','label':'Front wing','current':25,'proposed':26,'baseline_laps':[1,2,3],'baseline_setup':info['setup'],'created_session_time_s':1}
        r=analyse(info,laps,traces,{'trial':trial});self.assertIsNone(r['proposal']);self.assertEqual(r['trial']['status'],'waiting')

if __name__=='__main__':unittest.main()
