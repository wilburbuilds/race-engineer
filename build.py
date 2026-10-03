from pathlib import Path
import plistlib
import struct
import shutil
import subprocess

root=Path(__file__).resolve().parent
app=root/'dist'/'Race Engineer.app'
contents=app/'Contents'; resources=contents/'Resources'; macos=contents/'MacOS'
resources.mkdir(parents=True,exist_ok=True); macos.mkdir(parents=True,exist_ok=True)
backend=resources/'backend'; backend.mkdir(exist_ok=True)
for name in ('engineer.py','f1_udp.py','dashboard.html','app_backend.py','bootstrap.py','opponent_traces.py','coaching.py','setup_baseline.py'):
    shutil.copy2(root/name,backend/name)
runtime=Path.home()/'.local/share/uv/python/cpython-3.11.16-macos-aarch64-none'
if not (resources/'python').exists():
    shutil.copytree(runtime,resources/'python',symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
subprocess.run(['swiftc','-O','-module-cache-path',str(root/'swift-cache'),'-framework','Cocoa','-framework','WebKit',str(root/'App.swift'),'-o',str(macos/'RaceEngineer')],check=True)
icons=root/'RaceEngineer.iconset'; icons.mkdir(exist_ok=True)
for size in (16,32,128,256,512):
    for scale in (1,2):
        name=f'icon_{size}x{size}'+('@2x' if scale==2 else '')+'.png'
        subprocess.run(['sips','-z',str(size*scale),str(size*scale),str(root/'icon.png'),'--out',str(icons/name)],stdout=subprocess.DEVNULL,check=True)
chunks = b''
for kind, name in ((b'ic07','icon_128x128.png'),(b'ic08','icon_256x256.png'),(b'ic09','icon_512x512.png'),(b'ic10','icon_512x512@2x.png')):
    image = (icons/name).read_bytes()
    chunks += kind + struct.pack('>I',len(image)+8) + image
(resources/'RaceEngineer.icns').write_bytes(b'icns'+struct.pack('>I',len(chunks)+8)+chunks)
info={'CFBundleExecutable':'RaceEngineer','CFBundleIdentifier':'com.wilsensing.raceengineer','CFBundleName':'Race Engineer','CFBundleDisplayName':'Race Engineer','CFBundlePackageType':'APPL','CFBundleShortVersionString':'1.5.0','CFBundleVersion':'6','CFBundleIconFile':'RaceEngineer','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSLocalNetworkUsageDescription':'Receive F1 telemetry from your game console on your local network.','NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
print(app)
