#!/usr/bin/env python3
"""Update both existing Pis from the current checkout. Standard library + system SSH only."""
import argparse
import base64
import concurrent.futures
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
REMOTE=Path(__file__).with_name('pi_update_remote.py')
HOSTS={'roof':'192.168.2.4','robot':'192.168.2.3'}
ROOF_ONLY={'winch.py','x_axis.py','outrigger.py','stepper.py'}
ROBOT_ONLY={'camera.py','tap.py','tap_audio.py','led.py'}


def access_file(explicit=None,root=ROOT):
    if explicit:return Path(explicit).expanduser().resolve()
    configured=os.environ.get('BRICKMAPPER_PI_ACCESS')
    if configured:return Path(configured).expanduser().resolve()
    candidates=[Path.home()/'.config/brickmapper/pi-access.json']
    candidates += [parent/'outputs/Brick Mapper handoff 2026-11-18/private/pi-access.json' for parent in (root,*root.parents)]
    for candidate in candidates:
        if candidate.is_file():return candidate
    raise RuntimeError('Pi login settings were not found. Set BRICKMAPPER_PI_ACCESS to your private pi-access.json file.')


def load_access(path):
    data=json.loads(path.read_text())
    for role,address in HOSTS.items():
        item=data[role]
        if item.get('address')!=address or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',item.get('username','')):
            raise ValueError('Pi login settings do not match the product Ethernet setup.')
        for key in ('password','sudo_password'):
            value=item.get(key,'')
            if not isinstance(value,str) or '\n' in value or '\x00' in value:raise ValueError('Invalid Pi login settings.')
    return data


def snapshot(root=ROOT):
    plans={role:{} for role in HOSTS}
    for path in sorted((root/'app').iterdir()):
        if path.is_file() and path.suffix in ('.py','.js','.html'):
            plans['roof']['app/'+path.name]=path.read_bytes()
    for path in sorted((root/'pi').glob('*.py')):
        if path.name.startswith('flash-'):continue
        data=path.read_bytes()
        compile(data,str(path),'exec')
        for role in HOSTS:
            excluded=ROBOT_ONLY if role=='roof' else ROOF_ONLY
            if path.name not in excluded:plans[role]['controller/'+path.name]=data
    if not plans['roof'] or not plans['robot']:raise RuntimeError('The local app and Pi source folders are incomplete.')
    return plans


class Connection:
    def __init__(self,access,path,temporary):
        self.access=access
        helper=temporary/'askpass'
        helper.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(Path(__file__).resolve()))+
                          ' --askpass '+shlex.quote(str(path))+' "$@"\n')
        helper.chmod(0o700)
        known=temporary/'known_hosts'
        sources=[Path.home()/'.ssh/known_hosts',path.parent/'roof-known_hosts',path.parent/'robot-known_hosts']
        known.write_text('\n'.join(source.read_text() for source in sources if source.is_file())+'\n')
        known.chmod(0o600)
        self.known=known
        self.env={**os.environ,'SSH_ASKPASS':str(helper),'SSH_ASKPASS_REQUIRE':'force','DISPLAY':os.environ.get('DISPLAY',':0')}

    def ssh(self,role,command,content=b'',timeout=15):
        item=self.access[role]
        args=['ssh','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(self.known),
              '-o','ConnectTimeout=4','-o','ConnectionAttempts=1','-o','NumberOfPasswordPrompts=1',
              item['username']+'@'+item['address'],command]
        try:result=subprocess.run(args,input=content,capture_output=True,env=self.env,timeout=timeout)
        except subprocess.TimeoutExpired:raise RuntimeError('Connection timed out. Connect both powered Pis to the Ethernet switch and try again.') from None
        if result.returncode:
            message=result.stderr.decode(errors='replace').strip() or 'Pi update command failed.'
            for entry in self.access.values():
                if isinstance(entry,dict):
                    for key in ('password','sudo_password'):
                        if entry.get(key):message=message.replace(entry[key],'[hidden]')
            raise RuntimeError(message[-1500:])
        return result.stdout

    def probe(self,role,files):
        names=base64.b64encode(json.dumps(list(files)).encode()).decode()
        command='python3 -c '+shlex.quote(REMOTE.read_text())+' probe '+role+' '+shlex.quote(names)
        return json.loads(self.ssh(role,command))

    def apply(self,role,files,probe):
        remote='.cache/brickmapper-update-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        manifest={'role':role,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},'expected':probe['hashes']}
        packed=io.BytesIO()
        with tarfile.open(fileobj=packed,mode='w:gz') as archive:
            entries={'apply.py':REMOTE.read_bytes(),'manifest.json':json.dumps(manifest).encode(),
                     **{'payload/'+name:data for name,data in files.items()}}
            for name,data in entries.items():
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600
                archive.addfile(info,io.BytesIO(data))
        self.ssh(role,'umask 077; mkdir -p '+shlex.quote(remote)+' && tar -xzf - -C '+shlex.quote(remote),packed.getvalue())
        command="sudo -S -p '' python3 "+shlex.quote(remote+'/apply.py')+' apply '+role+' '+shlex.quote(remote)
        item=self.access[role]
        password=item.get('sudo_password',item.get('password',''))
        result=json.loads(self.ssh(role,command,(password+'\n').encode(),timeout=45))
        # This directory contains only this invocation's temporary software payload.
        try:self.ssh(role,'rm -rf -- '+shlex.quote(remote),timeout=8)
        except RuntimeError:pass  # Cleanup trouble does not undo a verified update.
        return result


def update_pair(connection,plans):
    probes={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(connection.probe,role,files):role for role,files in plans.items()}
        errors=[]
        for future in concurrent.futures.as_completed(pending):
            role=pending[future]
            try:probes[role]=future.result();print(role.capitalize()+' Pi connected.',flush=True)
            except Exception as error:errors.append(role.capitalize()+': '+str(error))
    if errors:raise RuntimeError('Neither Pi was updated.\n'+'\n'.join(errors))
    results={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(connection.apply,role,files,probes[role]):role for role,files in plans.items()}
        for future in concurrent.futures.as_completed(pending):
            role=pending[future]
            try:
                results[role]=future.result()
                changed=len(results[role]['changed'])
                print(role.capitalize()+(' Pi updated: '+str(changed)+' changed files.' if changed else ' Pi is already current.'),flush=True)
            except Exception as error:
                results[role]={'verified':False,'error':str(error)}
                print(role.capitalize()+' update failed: '+str(error),flush=True)
    return results


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--askpass':
        path,prompt=Path(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else ''
        access=load_access(path)
        if 'password' in prompt.lower():
            for role in HOSTS:
                item=access[role]
                if item['address'] in prompt and item['username'] in prompt:
                    print(item.get('password',''));return 0
        return 1
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access',help='Private Pi login JSON; found automatically on this Mac.')
    args=parser.parse_args()
    print('BrickMapper updater\nConnect both powered Pis and this Mac to the inspection Ethernet.\nRun while inspection is stopped. Reading the latest local software…\n',flush=True)
    try:
        path=access_file(args.access);access=load_access(path);plans=snapshot()
        with tempfile.TemporaryDirectory(prefix='brickmapper-updater-') as temporary:
            results=update_pair(Connection(access,path,Path(temporary)),plans)
        log=ROOT/'.update-logs';log.mkdir(exist_ok=True)
        (log/'latest.json').write_text(json.dumps({'updated_at':datetime.now(timezone.utc).isoformat(),'results':results},indent=2)+'\n')
        if not all(value.get('verified') for value in results.values()):return 1
        print('\nBoth Pis are current. Refresh http://192.168.2.4/ in your browser.\nSaved settings and Arduino firmware were kept.',flush=True)
        return 0
    except Exception as error:
        print('\nUpdate stopped: '+str(error),flush=True);return 1


if __name__=='__main__':sys.exit(main())
