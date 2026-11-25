"""Existing-install updater run over SSH by update_pis.py; never a network installer."""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.request

ADDRESSES={'roof':'192.168.2.4','robot':'192.168.2.3'}
CONTROL='blueteam-network.service'
WEB='brick-mapper.service'
CAMERA='bridge-usb-camera.service'


def target(role,name,root=Path('/')):
    parts=PurePosixPath(name).parts
    if len(parts)!=2 or parts[1] in ('.','..') or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.(py|js|html)',parts[1]):
        raise ValueError('Invalid software update path.')
    group,filename=parts
    if group=='controller' and filename.endswith('.py'):
        return root/'opt/blueteam-network'/filename
    if group=='app' and role=='roof':
        return root/'opt/brick-mapper/app'/filename
    raise ValueError('This update cannot modify configuration, credentials or firmware.')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def ensure_idle(status,role):
    if status.get('role')!=role:raise RuntimeError('The connected Pi has the wrong controller role.')
    if (any((status.get(axis) or {}).get('moving') for axis in ('xAxis','yAxis')) or
            (status.get('relays') or {}).get('busy') or
            (status.get('tap') or {}).get('mode','idle')!='idle' or
            (status.get('poweroff') or {}).get('scheduled') or
            (status.get('poweroff') or {}).get('robotScheduled')):
        raise RuntimeError('An inspection, movement, tap or shutdown is running. Stop it, then click Update again.')


class Runtime:
    root=Path('/')

    def service(self,action,names):
        subprocess.run(['systemctl',action,*names],check=True,capture_output=True,text=True,timeout=15)

    def active(self,name):
        return subprocess.run(['systemctl','is-active','--quiet',name],capture_output=True,timeout=3).returncode==0

    def identity(self,role):
        result=subprocess.run(['systemctl','show',CONTROL,'--property=ExecStart','--value'],
                              check=True,capture_output=True,text=True,timeout=3)
        if not re.search(r'--role\s+'+re.escape(role)+r'\b',result.stdout):
            raise RuntimeError('This Pi does not have the expected existing BrickMapper installation.')

    def get(self,url,headers=None):
        request=urllib.request.Request(url,headers=headers or {})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=.8) as response:
            return response.read(262144)

    def status(self,role):
        key=(self.root/'opt/blueteam-network/network.key').read_text().strip()
        # Controllers permit localhost and the peer Pi, not their own LAN address.
        connection=http.client.HTTPConnection(ADDRESSES[role],8091,timeout=.8,source_address=('127.0.0.1',0))
        try:
            connection.request('GET','/api/status',headers={'X-Network-Key':key})
            response=connection.getresponse()
            if response.status!=200:raise RuntimeError('Controller status is unavailable.')
            return json.loads(response.read(262144))
        finally:
            connection.close()

    def verify(self,role,hashes):
        deadline=time.monotonic()+6
        while True:
            try:
                ensure_idle(self.status(role),role)
                if role=='roof':
                    app=json.loads(self.get('http://192.168.2.4/api/app'))
                    if app.get('app')!='blue-team-brick-map':raise RuntimeError('Dashboard is not ready.')
                    for name in ('tap-score.js','tap-analysis.js'):
                        expected=hashes.get('app/'+name)
                        if expected and hashlib.sha256(self.get('http://192.168.2.4/'+name)).hexdigest()!=expected:
                            raise RuntimeError('The dashboard is not serving the new scoring files.')
                return
            except Exception:
                if time.monotonic()>=deadline:raise
                time.sleep(.25)


def probe(role,names,runtime=None):
    runtime=runtime or Runtime()
    if role not in ADDRESSES:raise ValueError('Unknown Pi role.')
    runtime.identity(role)
    active={name:runtime.active(name) for name in (CONTROL,WEB,CAMERA)}
    if active[CONTROL]:ensure_idle(runtime.status(role),role)
    hashes={name:digest(target(role,name,runtime.root)) for name in names}
    return {'role':role,'hostname':socket.gethostname(),'hashes':hashes,'active':active}


def restart_services(role,changed,active):
    services=[]
    if role=='robot' and 'controller/camera.py' in changed and active.get(CAMERA):services.append(CAMERA)
    if any(name.startswith('controller/') for name in changed) or not active.get(CONTROL):services.append(CONTROL)
    if role=='roof' and (any(name.startswith('app/') and name.endswith('.py') for name in changed) or not active.get(WEB)):
        services.append(WEB)
    return services


def apply(role,stage,runtime=None):
    runtime=runtime or Runtime()
    manifest=json.loads((stage/'manifest.json').read_text())
    if manifest['role']!=role:raise ValueError('Payload role does not match this Pi.')
    files=manifest['files'];expected=manifest['expected']
    current=probe(role,files,runtime)
    if current['hashes']!=expected:
        raise RuntimeError('Pi software changed during staging. Click Update again to reload it.')
    changed=[]
    for name,wanted in files.items():
        source=stage/'payload'/name
        destination=target(role,name,runtime.root)
        if source.is_symlink() or destination.is_symlink() or digest(source)!=wanted:
            raise RuntimeError('Update checksum or file-type check failed.')
        if name.endswith('.py'):compile(source.read_bytes(),str(destination),'exec')
        if current['hashes'][name]!=wanted:changed.append(name)
    services=restart_services(role,changed,current['active'])
    # Keep only changed files in memory for rollback. No growing backup folders.
    previous={name:(target(role,name,runtime.root).read_bytes(),stat.S_IMODE(target(role,name,runtime.root).stat().st_mode))
              if target(role,name,runtime.root).exists() else None for name in changed}
    try:
        stopping=[name for name in services if name in (CONTROL,CAMERA) and current['active'].get(name)]
        if stopping:runtime.service('stop',stopping)
        for name in changed:
            destination=target(role,name,runtime.root)
            destination.parent.mkdir(parents=True,exist_ok=True)
            temporary=destination.with_name(destination.name+'.updating')
            temporary.write_bytes((stage/'payload'/name).read_bytes())
            temporary.chmod(0o644)
            os.replace(temporary,destination)
        for service in services:runtime.service('restart',[service])
        runtime.verify(role,files)
        for service in services:
            if not runtime.active(service):raise RuntimeError(service+' did not stay running.')
    except Exception as error:
        for name in changed:
            destination=target(role,name,runtime.root)
            if previous[name] is None:destination.unlink(missing_ok=True)
            else:
                data,mode=previous[name]
                temporary=destination.with_name(destination.name+'.updating')
                temporary.write_bytes(data);temporary.chmod(mode);os.replace(temporary,destination)
        for service in services:
            try:runtime.service('restart' if current['active'].get(service) else 'stop',[service])
            except Exception:pass
        raise RuntimeError('Update failed; previous software was restored: '+str(error)) from error
    return {'role':role,'changed':changed,'restarted':services,'verified':True}


def main():
    operation,role,argument=sys.argv[1:]
    if operation=='probe':result=probe(role,json.loads(base64.b64decode(argument)))
    elif operation=='apply':result=apply(role,Path(argument))
    else:raise ValueError('Unknown updater operation.')
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr);sys.exit(1)
