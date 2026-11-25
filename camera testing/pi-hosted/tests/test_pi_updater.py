"""Offline updater checks: temporary files and fake Pis only; no network or motors."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/(name+'.py'))
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    return loaded


remote=module('pi_update_remote')
host=module('update_pis')


class FakeRuntime:
    def __init__(self,root,role='roof'):
        self.root=root;self.role=role;self.calls=[];self.fail=False
        self.services={remote.CONTROL:True,remote.WEB:True,remote.CAMERA:True}
        self.state={'role':role,'relays':{'busy':False},'tap':{'mode':'idle'}}
    def identity(self,role):
        if role!=self.role:raise RuntimeError('Wrong Pi role')
    def active(self,name):return self.services[name]
    def status(self,_role):return self.state
    def service(self,action,names):
        self.calls.append((action,list(names)))
        for name in names:self.services[name]=action!='stop'
    def verify(self,_role,_files):
        if self.fail:raise RuntimeError('New service failed to start')


class UpdaterTests(unittest.TestCase):
    def stage(self,root,files,role='roof'):
        stage=root/'staging';stage.mkdir()
        expected={}
        for name,data in files.items():
            path=stage/'payload'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            expected[name]=remote.digest(remote.target(role,name,root))
        (stage/'manifest.json').write_text(json.dumps({'role':role,'expected':expected,
            'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}))
        return stage

    def test_each_click_reads_latest_source_and_excludes_settings_keys_and_firmware(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'app').mkdir();(root/'pi').mkdir()
            score=root/'app/tap-score.js';score.write_text('latest one')
            for name in ['agent.py','winch.py','tap.py','new_helper.py','flash-board.py']:(root/'pi'/name).write_text('VALUE=1\n')
            (root/'pi/network.key').write_text('test-secret');(root/'pi/x-axis.json').write_text('{}')
            first=host.snapshot(root);score.write_text('latest two');second=host.snapshot(root)
            self.assertNotEqual(first['roof']['app/tap-score.js'],second['roof']['app/tap-score.js'])
            self.assertNotIn('controller/winch.py',second['robot']);self.assertNotIn('controller/tap.py',second['roof'])
            for role in ('roof','robot'):
                self.assertIn('controller/new_helper.py',second[role])
                self.assertTrue(all('network.key' not in name and not name.endswith('.json') and 'flash-board' not in name for name in second[role]))

    def test_updater_cannot_overwrite_configuration_keys_or_firmware(self):
        for name in ['../etc/shadow','controller/network.key','controller/x-axis.json','firmware/X.hex','app/../../x.py','/app/server.py']:
            with self.subTest(name=name),self.assertRaises(ValueError):remote.target('roof',name)
        with self.assertRaises(ValueError):remote.target('robot','app/server.py')

    def test_movement_tapping_and_wrong_roles_are_rejected(self):
        for changes in [{'relays':{'busy':True}},{'xAxis':{'moving':True}},{'yAxis':{'moving':True}}, {'tap':{'mode':'burst'}},{'role':'robot'}]:
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):remote.ensure_idle({'role':'roof',**changes},'roof')

    def test_one_offline_pi_prevents_changes_to_either_pi(self):
        class Connection:
            calls=[]
            def probe(self,role,_files):
                if role=='robot':raise RuntimeError('offline')
                return {}
            def apply(self,*args):self.calls.append(args)
        connection=Connection()
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):host.update_pair(connection,{'roof':{},'robot':{}})
        self.assertEqual(connection.calls,[])

    def test_unchanged_files_do_not_restart_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=remote.target('roof','app/tap-score.js',root)
            file.parent.mkdir(parents=True);file.write_bytes(b'same')
            stage=self.stage(root,{'app/tap-score.js':b'same'});runtime=FakeRuntime(root)
            result=remote.apply('roof',stage,runtime)
            self.assertEqual(result['changed'],[]);self.assertEqual(runtime.calls,[])

    def test_frontend_update_keeps_services_and_saved_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=remote.target('roof','app/tap-score.js',root)
            file.parent.mkdir(parents=True);file.write_bytes(b'old')
            config=root/'etc/brick-mapper/x-axis.json';config.parent.mkdir(parents=True);config.write_bytes(b'keep-settings')
            stage=self.stage(root,{'app/tap-score.js':b'new'});runtime=FakeRuntime(root)
            result=remote.apply('roof',stage,runtime)
            self.assertEqual(file.read_bytes(),b'new');self.assertEqual(config.read_bytes(),b'keep-settings')
            self.assertTrue(result['verified']);self.assertEqual(runtime.calls,[])

    def test_controller_update_restarts_controller_and_keeps_hardware_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=remote.target('roof','controller/agent.py',root)
            file.parent.mkdir(parents=True);file.write_bytes(b'VALUE=1\n')
            unit=root/'etc/systemd/system/blueteam-network.service';unit.parent.mkdir(parents=True)
            unit.write_bytes(b'ExecStart=agent.py --role roof --live-winch\n')
            stage=self.stage(root,{'controller/agent.py':b'VALUE=2\n'});runtime=FakeRuntime(root)
            remote.apply('roof',stage,runtime)
            self.assertEqual(runtime.calls,[('stop',[remote.CONTROL]),('restart',[remote.CONTROL])])
            self.assertIn(b'--live-winch',unit.read_bytes())

    def test_robot_camera_update_restarts_camera_and_robot_controller_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=remote.target('robot','controller/camera.py',root)
            file.parent.mkdir(parents=True);file.write_bytes(b'VALUE=1\n')
            stage=self.stage(root,{'controller/camera.py':b'VALUE=2\n'},role='robot')
            runtime=FakeRuntime(root,role='robot')
            result=remote.apply('robot',stage,runtime)
            self.assertTrue(result['verified'])
            self.assertEqual(result['restarted'],[remote.CAMERA,remote.CONTROL])
            self.assertNotIn(('restart',[remote.WEB]),runtime.calls)

    def test_failed_restart_restores_changed_files_and_removes_new_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=remote.target('roof','controller/agent.py',root)
            file.parent.mkdir(parents=True);file.write_bytes(b'VALUE=1\n');file.chmod(0o640)
            stage=self.stage(root,{'controller/agent.py':b'VALUE=2\n','controller/new_helper.py':b'VALUE=3\n'})
            runtime=FakeRuntime(root);runtime.fail=True
            with self.assertRaisesRegex(RuntimeError,'previous software was restored'):remote.apply('roof',stage,runtime)
            self.assertEqual(file.read_bytes(),b'VALUE=1\n');self.assertEqual(file.stat().st_mode & 0o777,0o640)
            self.assertFalse(remote.target('roof','controller/new_helper.py',root).exists());self.assertTrue(runtime.active(remote.CONTROL))


if __name__=='__main__':unittest.main()
