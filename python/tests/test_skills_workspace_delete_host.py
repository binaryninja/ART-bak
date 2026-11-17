"""Synthetic private Skills deletion authority, transport and ownership tests.

No user data. Unknown-case teardown releases test fixtures only and is never
evidence of production reconciliation, rollback or clean ownership release.
"""
import io
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

from artex import skills_workspace as host
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.auth import sign_jwt
import test_skills_workspace_host as fixtures
import test_skills_workspace_transport as transport


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_delete_opt_in_requires_exact_boolean_and_write_opt_in(self):
        for value,writes in ((True,False),(1,True),('yes',True),(None,True)):
            config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,writes,enable_deletes=value)
            with patch.object(host.RuntimeLease,'acquire') as acquire:
                with self.assertRaises(ValueError):host.SkillsWorkspaceHost.prepare(config)
                acquire.assert_not_called()

    def test_default_profile_stays_legacy_and_explicit_deletes_add_true_only(self):
        for deletes in (False,True):
            config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,enable_deletes=deletes)
            instance=self.f.prepare(config)
            status,body,headers=self.f.request(instance,'/api/skills-workspace')
            expected={'profile':'skills-workspace','writes_enabled':True}
            if deletes:expected['deletes_enabled']=True
            self.assertEqual((status,json.loads(body)),(200,expected))
            self.assertEqual(headers['Cache-Control'],'no-store')
            hs,empty,hh=self.f.request(instance,'/api/skills-workspace','HEAD')
            self.assertEqual((hs,empty,hh['Content-Length']),(200,b'',str(len(body))))
            self.assertTrue(instance.stop().stopped)

    def test_post_header_delete_mode_drift_withholds_hint_without_granting_authority(self):
        config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,enable_deletes=True)
        instance=self.f.prepare(config)
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/skills-workspace','CONTENT_LENGTH':'0',
             'wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        statuses=[]
        def start(status,headers):
            statuses.append(status)
            object.__setattr__(config,'enable_deletes',False)
        try:
            self.assertEqual(b''.join(instance.application(env,start)),b'')
            self.assertEqual(statuses,['200 OK'])
            self.assertFalse(instance._unknown)
        finally:object.__setattr__(config,'enable_deletes',True)
        self.assertTrue(instance.stop().stopped)

    def test_cli_delete_flag_without_write_flag_refuses_before_host_preparation(self):
        with patch.object(host.SkillsWorkspaceHost,'prepare') as prepare,patch('sys.stderr',new=io.StringIO()):
            self.assertEqual(host.main(['--root',str(self.f.root),'--password-file',str(self.f.password),
                                       '--exclusive-owner','--enable-deletes']),1)
            prepare.assert_not_called()

    def test_unknown_delete_and_abandoned_success_quarantine_not_clean_release(self):
        for outcome in ('500','abandoned','exception'):
            instance=self.f.prepare()
            def delegate(env,start):
                if outcome=='exception':raise RuntimeError('synthetic private fault')
                start('500 Internal Server Error' if outcome=='500' else '200 OK',[])
                return [b'{"deleted":"synthetic"}\n']
            instance._application=delegate
            if outcome=='abandoned':
                env={'REQUEST_METHOD':'DELETE','PATH_INFO':'/api/skills/sample/files/synthetic',
                     'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
                body=instance.application(env,lambda *args:None);next(body);body.close()
            else:
                self.f.request(instance,'/api/skills/sample/files/synthetic','delete')
            self.assertTrue(instance._unknown)
            self.assertEqual(instance.stop(0).reason,'mutation_unknown')
            with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()
            # This loop deliberately reconciles only its inert mocked fixture.
            instance._lease.release(owner=instance,permit=instance._permit)

    def test_safe_delete_refusals_do_not_quarantine(self):
        instance=self.f.prepare()
        for code in (400,401,403,404,405,409):
            def delegate(env,start):start(str(code)+' Refused',[]);return [b'{}']
            instance._application=delegate
            self.assertEqual(self.f.request(instance,'/api/skills/sample/files/synthetic','DELETE')[0],code)
            self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop().stopped)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.t.config=host.WorkspaceConfig(str(self.t.f.root),str(self.t.f.password),True,True,
                                          self.t.port,enable_deletes=True)

    def delete(self,path,token=None,headers=None):
        return self.t.send('/api/skills/sample/files/'+quote(path,safe='/'),'DELETE',token=token,headers=headers)

    def test_actual_authenticated_file_and_empty_folder_deletion_then_clean_stop(self):
        file=self.t.f.skill/'削除.txt';file.write_text('Synthetic only')
        folder=self.t.f.skill/'空';folder.mkdir()
        instance=self.t.start()
        self.assertEqual(self.delete(file.name)[0],401);self.assertTrue(file.exists())
        token=self.t.login()
        profile=json.loads(self.t.send('/api/skills-workspace',token=token)[1])
        self.assertEqual(profile,{'profile':'skills-workspace','writes_enabled':True,'deletes_enabled':True})
        for path in (file.name,folder.name):
            status,body,_=self.delete(path,token)
            self.assertEqual((status,json.loads(body)),(200,{'deleted':path}))
            self.assertFalse((self.t.f.skill/path).exists())
            self.assertEqual(self.delete(path,token)[0],404)
        self.assertTrue((self.t.f.skill/'SKILL.md').is_file())
        self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop(3).stopped)
        lease=RuntimeLease(instance._lease.path);self.assertTrue(lease.acquire());lease.release()

    def test_write_flag_does_not_infer_delete_authority(self):
        self.t.config=host.WorkspaceConfig(str(self.t.f.root),str(self.t.f.password),True,True,self.t.port)
        target=self.t.f.skill/'preserved';target.write_text('Synthetic unchanged')
        instance=self.t.start();token=self.t.login()
        self.assertEqual(self.delete('preserved',token)[0],403)
        self.assertEqual(target.read_text(),'Synthetic unchanged')
        self.assertNotIn('deletes_enabled',json.loads(self.t.send('/api/skills-workspace',token=token)[1]))
        self.assertFalse(instance._unknown);self.assertTrue(instance.stop(3).stopped)

    def test_manifest_nonempty_symlink_hardlink_and_host_origin_refused_without_effect(self):
        parent=self.t.f.skill
        (parent/'nonempty').mkdir();(parent/'nonempty'/'child').write_text('keep')
        outside=self.t.f.path/'outside';outside.write_text('Synthetic outside unchanged')
        (parent/'alias').symlink_to(outside);os.link(outside,parent/'hard')
        os.mkfifo(parent/'fifo')
        instance=self.t.start();token=self.t.login()
        for path,status in [('SKILL.md',403),('skill.md',403),('nonempty',409),('alias',400),('hard',400),('fifo',400)]:
            self.assertEqual(self.delete(path,token)[0],status,path)
        self.assertEqual((parent/'nonempty'/'child').read_text(),'keep')
        self.assertEqual(outside.read_text(),'Synthetic outside unchanged')
        for fields in ({'Host':'evil.invalid'},{'Origin':'null'},{'Origin':'https://evil.invalid'},
                       {'Host':'evil.invalid','X-Forwarded-Host':f'127.0.0.1:{self.t.port}'}):
            self.assertEqual(self.delete('SKILL.md',token,fields)[0],403)
        self.assertTrue((parent/'SKILL.md').is_file())
        self.assertFalse(instance._unknown);self.assertTrue(instance.stop(3).stopped)

    def test_actual_post_unlink_fsync_failure_is_unknown_with_lease_retained(self):
        target=self.t.f.skill/'removed';target.write_text('Synthetic only')
        identity=(self.t.f.skill.stat().st_dev,self.t.f.skill.stat().st_ino)
        instance=self.t.start();token=self.t.login();original=os.fsync
        def fail(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==identity:raise OSError('synthetic private fsync failure')
            return original(fd)
        with patch('os.fsync',side_effect=fail):status,body,_=self.delete(target.name,token)
        self.assertEqual(status,500)
        self.assertEqual((json.loads(body)['outcome'],json.loads(body)['retry']),('unknown',False))
        self.assertNotIn(b'synthetic private',body);self.assertFalse(target.exists())
        self.assertEqual(self.t.send('/api/skills-workspace',token=token)[0],503)
        self.assertEqual(instance.stop(3).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_actual_disconnect_after_removal_retains_unacknowledged_delete(self):
        target=self.t.f.skill/'unacknowledged';target.write_text('Synthetic only')
        identity=(self.t.f.skill.stat().st_dev,self.t.f.skill.stat().st_ino)
        instance=self.t.start();token=self.t.login()
        entered=threading.Event();release=threading.Event();original=os.fsync
        def hold(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==identity:
                entered.set()
                if not release.wait(5):raise OSError('Synthetic bounded release timeout')
            return original(fd)
        client=socket.create_connection(('127.0.0.1',self.t.port),timeout=3)
        try:
            with patch('os.fsync',side_effect=hold):
                request=(f'DELETE /api/skills/sample/files/{target.name} HTTP/1.1\r\nHost: 127.0.0.1:{self.t.port}\r\n'
                         f'Authorization: Bearer {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n').encode()
                client.sendall(request);self.assertTrue(entered.wait(3));self.assertFalse(target.exists())
                client.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0));client.close();release.set()
                with instance._state:self.assertTrue(instance._state.wait_for(lambda:not instance._active,4))
            self.assertTrue(instance._unknown);self.assertEqual(instance.stop(3).reason,'mutation_unknown')
            with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()
        finally:release.set();client.close()

    def test_actual_explicit_cli_delete_and_natural_shutdown(self):
        target=self.t.f.skill/'cli-file';target.write_text('Synthetic only')
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONPATH':os.pathsep.join(sys.path),
             'HOME':str(self.t.f.path),'TMPDIR':str(self.t.f.path),'PYTHONNOUSERSITE':'1',
             'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
        command=[sys.executable,'-m','artex.skills_workspace','--root',str(self.t.f.root),
                 '--password-file',str(self.t.f.password),'--exclusive-owner','--enable-writes',
                 '--enable-deletes','--port',str(self.t.port)]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        try:
            readable,_,_=select.select([child.stdout],[],[],8);self.assertTrue(readable)
            line=child.stdout.readline();self.assertIn(b'Skills workspace ready',line)
            token=self.t.login();status,body,_=self.delete(target.name,token)
            self.assertEqual((status,json.loads(body)),(200,{'deleted':target.name}));self.assertFalse(target.exists())
            os.kill(child.pid,signal.SIGTERM);out,err=child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,err.decode())
            self.assertNotIn(token.encode(),line+out+err);self.assertNotIn(b'synthetic-test-password',line+out+err)
            lease=RuntimeLease(str(self.t.f.root/'.artex-skills.lock'));self.assertTrue(lease.acquire());lease.release()
        finally:
            if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.communicate(timeout=5)


if __name__=='__main__':unittest.main()
