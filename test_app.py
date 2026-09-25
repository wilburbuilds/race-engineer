"""Integration test the bundled runtime, HTTP dashboard, single instance, and EOF shutdown."""
import json
import socket
import struct
import time
import f1_udp as f
from pathlib import Path
import subprocess
import tempfile
import urllib.request

root=Path(__file__).resolve().parent
resources=root/'dist/Race Engineer.app/Contents/Resources'
def packet(pid, layout, values):
    buf=bytearray(f.EXPECTED_SIZES[pid])
    def put(layout, start, fields):
        offset=start
        for name, code in layout.fields:
            if name in fields: struct.pack_into('<'+code,buf,offset,fields[name])
            offset+=struct.calcsize('<'+code)
    put(f.HEADER,0,{'packetFormat':2026,'packetId':pid,'sessionUID':123456,'playerCarIndex':0,'sessionTime':1.0})
    put(layout,f.HEADER.size,values)
    return buf
with tempfile.TemporaryDirectory() as temp:
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); sock.bind(('127.0.0.1',0)); udp_port=sock.getsockname()[1]; sock.close()
    cmd=[str(resources/'python/bin/python3.11'),'-I','-B','-u',str(resources/'backend/bootstrap.py'),'--data-dir',temp,'--udp-port',str(udp_port)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=proc.stdout.readline()
        assert line, proc.stderr.read()
        url=json.loads(line)['url']
        live=json.load(urllib.request.urlopen(url+'/api/live',timeout=5))
        assert 'comparison' in live
        html=urllib.request.urlopen(url,timeout=5).read().decode()
        assert 'You vs others' in html
        duplicate=subprocess.run(cmd,input='',capture_output=True,text=True,timeout=10)
        assert duplicate.returncode != 0 and 'already recording' in duplicate.stderr
        sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        participants=bytearray(f.EXPECTED_SIZES[f.PKT_PARTICIPANTS])
        header=packet(f.PKT_SESSION,f.SESSION_A,{})[:f.HEADER.size]
        participants[:f.HEADER.size]=header
        offset=0
        for name, code in f.HEADER.fields:
            if name=='packetId': struct.pack_into('<'+code,participants,offset,f.PKT_PARTICIPANTS)
            offset+=struct.calcsize('<'+code)
        participants[f.HEADER.size]=2
        for driver in range(2):
            offset=f.HEADER.size+1+driver*f.PARTICIPANT.size
            for name,code in f.PARTICIPANT.fields:
                if name=='name': struct.pack_into('<'+code,participants,offset,('Test '+str(driver)).encode())
                offset+=struct.calcsize('<'+code)
        for payload in (
            packet(f.PKT_SESSION,f.SESSION_A,{'trackId':10,'sessionType':10,'trackLength':7007}),
            participants,
            packet(f.PKT_LAP,f.LAP_DATA,{'carPosition':1,'currentLapNum':1,'driverStatus':1,'resultStatus':2,'lapDistance':100}),
            packet(f.PKT_TELEMETRY,f.CAR_TELEMETRY,{'speed':123,'gear':3,'throttle':.5}),
        ): sender.sendto(payload,('127.0.0.1',udp_port))
        sender.close()
        for _ in range(30):
            live=json.load(urllib.request.urlopen(url+'/api/live',timeout=5))
            if live.get('trace_rows',0)>0: break
            time.sleep(.1)
        assert live['trace_rows']==1,live
        catalog=json.load(urllib.request.urlopen(url+'/api/opponents',timeout=5))
        assert len(catalog['drivers'])==1 and catalog['drivers'][0]['laps'][0]['samples']==1,catalog
        trace=json.load(urllib.request.urlopen(url+'/api/opponent-trace?driver=0&lap=1',timeout=5))
        assert trace['trace']['rows'][0][2]==123
        proc.stdin.close()
        proc.wait(timeout=10)
        assert proc.returncode == 0, proc.stderr.read()
        recordings=list(Path(temp).glob('sessions/*/trace.csv'))
        assert len(recordings)==1 and len(recordings[0].read_text().splitlines())==2
        saved=json.loads((recordings[0].parent/'opponents.json').read_text())
        assert saved['drivers'][0]['laps'][0]['rows'][0][2]==123
        print('PASS: bundled Python, dashboard, comparison API, duplicate prevention, UDP capture, recording saved on EOF, shutdown')
    finally:
        if proc.poll() is None: proc.terminate(); proc.wait(timeout=10)
