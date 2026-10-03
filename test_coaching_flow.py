"""Exercise the real HTTP practice workflow with disposable synthetic recordings."""
import csv
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request
from engineer import DashboardServer, SessionLogger
from f1_udp import RaceState
from coaching import atomic_json
from test_coaching import fixture


def save(folder,info,laps,traces):
    atomic_json(folder/'session.json',info)
    with (folder/'laps.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(laps[0]));w.writeheader();w.writerows(laps)
    with (folder/'trace.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['lap']+list(traces[1][0]));w.writeheader()
        for lap,rows in traces.items():w.writerows(dict(row,lap=lap) for row in rows)

os.environ['RACE_ENGINEER_GROK']='off'
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);folder=root/'practice';folder.mkdir()
    info,laps,traces=fixture();info['last_session_time_s']=70
    save(folder,info,laps,traces)
    state=RaceState();logger=SessionLogger(state,root);logger.folder=folder
    server=DashboardServer(state,logger,0);url=server.url.replace(':0',':'+str(server.httpd.server_address[1]))
    def get():return json.load(urllib.request.urlopen(url+'/api/coaching',timeout=5))
    def post(fields):
        request=urllib.request.Request(url+'/api/coaching',json.dumps(fields).encode(),{'Content-Type':'application/json'},method='POST')
        return json.load(urllib.request.urlopen(request,timeout=5))
    def wait_for(predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            report=get()
            if predicate(report):return report
            time.sleep(.03)
        raise AssertionError(report)
    try:
        wait_for(lambda r:r.get('eligible_count')==3)
        assert post({'goal':'race'})['ok']
        wait_for(lambda r:r.get('goal')=='race')
        source=json.load(urllib.request.urlopen(url+'/api/setup-baseline',timeout=5))
        assert source['status']=='waiting' and 'track' in source['message']
        assert post({'symptom':'wont_turn'})['ok']
        report=wait_for(lambda r:r.get('proposal') is not None)
        assert report['proposal']['current']==25 and report['proposal']['proposed']==26
        post({'start_trial':True})
        wait_for(lambda r:r.get('trial',{}).get('status')=='waiting')
        info['setup']=dict(info['setup'],frontWing=26)
        info['setup_history'].append({'revision':2,'session_time_s':80,'lap':4,'setup':info['setup']})
        for i in range(3):
            original=laps[i]
            laps.append(dict(original,lap=i+4,setup_revision=2,time_ms=original['time_ms']-600))
            traces[i+4]=[dict(row,lap_time_ms=row['lap_time_ms']*.94,session_time_s=row['session_time_s']+100) for row in traces[i+1]]
        save(folder,info,laps,traces)
        result=wait_for(lambda r:r.get('trial',{}).get('status')=='evaluated')
        assert result['trial']['delta_ms']==-600,result['trial']
        assert result['trial']['best_delta_ms']==-600 and result['trial']['goal']=='race'
        assert 'Keep provisionally' in result['trial']['message']
        post({'clear_trial':True})
        wait_for(lambda r:r.get('trial') is None and r.get('lap_count')==6)
        logger.coaching.finish(folder)
        assert 'Next practice' in (folder/'debrief.md').read_text()
        print('PASS: HTTP feedback, proposal, start test, detected setup revision, median evaluation, end test, saved debrief')
    finally:
        server.httpd.shutdown();server.httpd.server_close()
