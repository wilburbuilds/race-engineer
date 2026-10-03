"""Published community starting setups. Fetching never blocks telemetry or HTTP."""
from __future__ import annotations
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from coaching import SETUP_MENU, atomic_json, load_json, number

TRACK_SLUGS = dict(zip(
    ['Melbourne','Shanghai','Bahrain','Barcelona','Monaco','Montreal','Silverstone','Hungaroring','Spa','Monza','Singapore','Suzuka','Abu Dhabi','Austin','Interlagos','Austria','Mexico','Baku','Zandvoort','Imola','Jeddah','Miami','Las Vegas','Qatar','Madrid'],
    ['australia','china','bahrain','spain','monaco','canada','britain','hungary','belgium','italy','singapore','japan','abu-dhabi','usa','brazil','austria','mexico','azerbaijan','netherlands','imola','saudi-arabia','miami','las-vegas','qatar','madrid']))
LABELS = {
 'frontWing':'Front Wing','rearWing':'Rear Wing',
 'onThrottle':'Differential Adjustment On Throttle','offThrottle':'Differential Adjustment Off Throttle',
 'engineBraking':'Engine Braking','frontCamber':'Front Camber','rearCamber':'Rear Camber',
 'frontToe':'Front Toe','rearToe':'Rear Toe','frontSuspension':'Front Suspension','rearSuspension':'Rear Suspension',
 'frontAntiRollBar':'Front Anti-Roll Bar','rearAntiRollBar':'Rear Anti-Roll Bar',
 'frontSuspensionHeight':'Front Ride Height','rearSuspensionHeight':'Rear Ride Height',
 'brakePressure':'Break Pressure','brakeBias':'Front Break Bias',
 'frontRightTyrePressure':'Front Right Tyre Pressure','frontLeftTyrePressure':'Front Left Tyre Pressure',
 'rearRightTyrePressure':'Rear Right Tyre Pressure','rearLeftTyrePressure':'Rear Left Tyre Pressure'}
BOUNDS = {k:(0,100) for k in LABELS}
BOUNDS.update(frontWing=(0,50),rearWing=(0,50),frontCamber=(-3.5,-2.5),rearCamber=(-2,-1),frontToe=(0,.5),rearToe=(0,.5),
              frontSuspension=(1,41),rearSuspension=(1,41),frontAntiRollBar=(1,21),rearAntiRollBar=(1,21),brakePressure=(50,100),brakeBias=(50,70))
for k in LABELS:
 if 'TyrePressure' in k: BOUNDS[k]=(20,30)

class SourceHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.fields={};self.tag=None;self.parts=[];self.label='';self.heading='';self.rows=[];self.row=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('dt','dd','h1'): self.tag=tag;self.parts=[]
        if tag=='tr':self.row={'url':None,'text':[]}
        if tag=='a' and self.row is not None and '/setups/' in attrs.get('href',''):self.row['url']=attrs['href']
    def handle_data(self,data):
        if self.tag:self.parts.append(data)
        if self.row is not None:self.row['text'].append(data)
    def handle_endtag(self,tag):
        if tag==self.tag:
            value=' '.join(' '.join(self.parts).split())
            if tag=='dt':self.label=value
            elif tag=='dd' and value:self.fields[self.label]=value
            elif tag=='h1':self.heading=value
            self.tag=None
        if tag=='tr' and self.row is not None:
            if self.row['url']:self.row['text']=' '.join(' '.join(self.row['text']).split());self.rows.append(self.row)
            self.row=None

def context(info,goal='qualifying'):
    track=info.get('track');regs=info.get('regulations_2026');weather=info.get('weather')
    if not track:return None,'Waiting for track telemetry. Start a practice session in the game.'
    if track not in TRACK_SLUGS:return None,'No community source is configured for this track layout.'
    if not isinstance(regs,bool):return None,'Car regulations are not known yet. Waiting for session telemetry.'
    if info.get('formula') not in (None,0,13):return None,'Community baselines currently support F1 cars.'
    if weather not in ('clear','light cloud','overcast','light rain','heavy rain','storm'):return None,'Waiting for weather telemetry.'
    return {'track':track,'slug':TRACK_SLUGS[track],'year':26 if regs else 25,'conditions':'wet' if weather in ('light rain','heavy rain','storm') else 'dry',
            'goal':goal,'assists':{k:info.get('assists',{}).get(k) for k in ('traction_control','abs')}},None

def fetch_html(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='www.f1laps.com':raise ValueError('Unrecognised setup source')
    req=Request(url,headers={'User-Agent':'RaceEngineer/1.5 (+local setup lookup)'})
    with urlopen(req,timeout=12) as response:
        if urlparse(response.url).hostname!='www.f1laps.com' or urlparse(response.url).path!=parsed.path:raise ValueError('Source redirected away from the requested setup')
        data=response.read(2_000_001)
        if len(data)>2_000_000:raise ValueError('Setup page is too large')
        return data.decode('utf-8')

def parse_setup(html,url,ctx):
    p=SourceHTML();p.feed(html);f=p.fields
    track_names=['united states','usa','texas'] if ctx['slug']=='usa' else [ctx['slug'].replace('-',' ')]
    if not any(name in p.heading.lower() for name in track_names) or f'F1 {ctx["year"]} ' not in p.heading or ctx['slug'] not in urlparse(url).path.split('/') or f.get('Conditions','').lower()!=ctx['conditions']:
        raise ValueError('Source game, track or weather does not match the session')
    values={}
    for key,label in LABELS.items():
        text=f.get(label)
        if text is None and key=='brakePressure':text=f.get('Brake Pressure')
        if text is None and key=='brakeBias':text=f.get('Front Brake Bias')
        if text is None:continue
        match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*[%˚°]?\s*',text)
        value=number(match.group(1)) if match else None
        low,high=BOUNDS[key]
        if value is None or not low<=value<=high:raise ValueError(f'Invalid published value for {label}')
        if key not in ('frontCamber','rearCamber','frontToe','rearToe') and 'TyrePressure' not in key and not value.is_integer():raise ValueError('Non-integer setup slider value')
        values[key]=value
    missing=[k for k in LABELS if k!='engineBraking' and k not in values]
    if missing:raise ValueError('Published setup is incomplete')
    return {'status':'ready','context':ctx,'title':p.heading,'source':'F1Laps community','url':url,
            'published':f.get('Date','Unknown'),'fetched_at':time.time(),'values':values,
            'metadata':{k:f.get(k,'Unknown') for k in ('Team','Session','Steering','Traction Control','Anti-Lock Brakes','Lap time')},
            'missing':['Engine braking is not published; retain your current value.'] if 'engineBraking' not in values else [],
            'note':'A published starting point, not a proven optimum for your car. Verify it with your own laps.'}

def lookup(ctx,fetch=fetch_html):
    base=f'https://www.f1laps.com/f1-{ctx["year"]}/setups/{ctx["slug"]}/'
    # Prefer the requested session and matching ABS/TC. Relax assists only with disclosure.
    session='4' if ctx['goal']=='qualifying' else '5'
    attempts=[{'conditions':ctx['conditions'],'session':session}]
    matched=dict(attempts[0])
    for key,remote in [('traction_control','a_tc'),('abs','a_abs')]:
        if ctx['assists'].get(key) is not None:matched[remote]=str(ctx['assists'][key])
    if matched!=attempts[0]:attempts.insert(0,matched)
    fallback={'conditions':ctx['conditions'],'session':'5' if session=='4' else '6'}
    attempts.append(fallback)
    for filters in attempts:
        page=SourceHTML();page.feed(fetch(base+'?'+urlencode(filters)))
        expected_session={'4':'Qualifying','5':'Race','6':'Time Trial'}[filters['session']]
        links=[]
        for row in page.rows:
            path=row['url']
            if re.fullmatch(re.escape(urlparse(base).path)+r'[0-9a-f-]{36}/',path) and path not in links:links.append(path)
        candidates=[]
        for path in links[:3]:
            try:
                candidate=parse_setup(fetch(urljoin(base,path)),urljoin(base,path),ctx)
                if candidate['metadata']['Session']!=expected_session:continue
                candidates.append(candidate)
            except (ValueError,OSError):continue
        if candidates:
            def rank(c):
                m=c['metadata'];a=ctx['assists'];tc={'Off':0,'Medium':1,'Full':2}.get(m['Traction Control']);absval={'Off':0,'On':1}.get(m['Anti-Lock Brakes'])
                return sum(w for k,v,w in [('traction_control',tc,2),('abs',absval,2)] if a.get(k) is not None and a[k]==v)
            chosen=max(candidates,key=rank);m=chosen['metadata']
            chosen['selection']=f'Compared {len(candidates)} published {expected_session.lower()} setups; preferred matching ABS and traction control. Listed lap times are self-reported.'
            mismatches=[]
            for key,label,convert in [('traction_control','Traction Control',{'Off':0,'Medium':1,'Full':2}),('abs','Anti-Lock Brakes',{'Off':0,'On':1})]:
                if ctx['assists'][key] is not None and convert.get(m[label])!=ctx['assists'][key]:mismatches.append(label)
            chosen['mismatches']=mismatches
            chosen['session_fallback']=expected_session!=('Qualifying' if ctx['goal']=='qualifying' else 'Race')
            return chosen
    raise ValueError('No complete matching community setup was found. Try another source or refresh later.')

class SetupBaselineService:
    def __init__(self,cache):
        self.cache=Path(cache);self.lock=threading.Lock();self.results={};self.pending={};self.wake=threading.Event();self.last_request={}
        threading.Thread(target=self._run,daemon=True,name='setup-source').start()
    def get(self,info,goal='qualifying',refresh=False):
        ctx,error=context(info,goal)
        if not ctx:return {'status':'waiting','message':error}
        key=hashlib.sha256(json.dumps(ctx,sort_keys=True).encode()).hexdigest()
        with self.lock:
            cached=self.results.get(key) or load_json(self.cache/(key+'.json'),{})
            expired=time.time()-cached.get('fetched_at',0)>7*86400
            if key not in self.pending and ((not cached or expired) or refresh) and time.time()-self.last_request.get(key,0)>60:
                self.last_request[key]=time.time();self.pending[key]=ctx;self.wake.set()
                self.results[key]=dict(cached,status='refreshing' if cached.get('values') else 'searching',context=ctx)
            result=dict(self.results.get(key) or cached or {'status':'searching','context':ctx})
        result['stale']=bool(result.get('values')) and expired
        result['menu']=[{'key':k,'label':label,'value':result.get('values',{}).get(k),'current':info.get('setup',{}).get(k)} for k,label in SETUP_MENU]
        return result
    def _run(self):
        while True:
            self.wake.wait()
            with self.lock:
                if not self.pending:self.wake.clear();continue
                key,ctx=next(iter(self.pending.items()))
            try:
                result=lookup(ctx);self.cache.mkdir(parents=True,exist_ok=True);atomic_json(self.cache/(key+'.json'),result)
            except Exception:
                old=load_json(self.cache/(key+'.json'),{})
                result=dict(old,status='ready' if old.get('values') else 'unavailable',context=ctx,
                            message='Online setup lookup is unavailable. Refresh to retry; cached values remain available when present.',fetched_at=old.get('fetched_at',time.time()))
            with self.lock:self.results[key]=result;self.pending.pop(key,None)
