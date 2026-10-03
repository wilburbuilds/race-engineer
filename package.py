"""Build and package the standalone Apple Silicon app for GitHub Releases."""
from pathlib import Path
import hashlib
import plistlib
import subprocess
import sys
import tempfile
import zipfile

root=Path(__file__).resolve().parent
subprocess.run([sys.executable,str(root/'build.py')],cwd=root,check=True)
app=root/'dist'/'Race Engineer.app'
with (app/'Contents/Info.plist').open('rb') as f:
    version=plistlib.load(f)['CFBundleShortVersionString']
archive=app.parent/f'Race-Engineer-{version}-macOS-arm64.zip'
subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(archive)],check=True)
with zipfile.ZipFile(archive) as z:
    if bad:=z.testzip():raise RuntimeError(f'Archive checksum failed: {bad}')
with tempfile.TemporaryDirectory(prefix='race-release-') as temp:
    subprocess.run(['ditto','-x','-k',str(archive),temp],check=True)
    unpacked=Path(temp)/'Race Engineer.app'
    subprocess.run(['codesign','--verify','--deep','--strict',str(unpacked)],check=True)
    python=unpacked/'Contents/Resources/python/bin/python3.11'
    subprocess.run([str(python),'-I','-B','-c',
                    'import sys;sys.path.insert(0,sys.argv[1]);import engineer,coaching,setup_baseline;print("Packaged backend imports verified")',
                    str(unpacked/'Contents/Resources/backend')],check=True)
checksum=archive.with_suffix('.zip.sha256')
checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
print(archive)
print(checksum)
