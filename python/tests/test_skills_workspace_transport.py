"""Synthetic loopback-only Skills host/CLI acceptance, no PG/provider/browser."""
import http.client
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from artex import skills_workspace as host
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
import test_skills_workspace_host as fixture_tests


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture_tests.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,self.port)

    def send(self,path,method='GET',data=None,token=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=4)
        fields={'Content-Type':'application/json',**(headers or {})}
        if token:fields['Authorization']='Bearer '+token
        conn.request(method,path,None if data is None else json.dumps(data),fields)
        response=conn.getresponse();result=(response.status,response.read(),dict(response.getheaders()));conn.close()
        return result

    def login(self):
        status,body,_=self.send('/api/auth/login','POST',{'username':'ARTEX','password':'synthetic-test-password'})
        self.assertEqual(status,200);return json.loads(body)['token']

    def start(self):
        instance=self.f.prepare(self.config);instance.start()
        self.addCleanup(lambda:instance.stop(3))
        return instance

    def test_real_http_boundaries_unicode_upsert_duplicate_directory_and_symlink(self):
        instance=self.start()
        self.assertEqual(self.send('/api/skills')[0],401);token=self.login()
        self.assertEqual(self.send('/api/skills/sample/files/lower','put',{'content':'bad'},token)[0],405)
        self.assertFalse((self.f.skill/'lower').exists())
        for fields in ({'Host':'evil.invalid'},{'Origin':'null'},{'Origin':'https://evil.invalid'},
                       {'Host':'evil.invalid','X-Forwarded-Host':f'127.0.0.1:{self.port}'}):
            self.assertEqual(self.send('/api/skills',token=token,headers=fields)[0],403)
        with socket.create_connection(('127.0.0.1',self.port),timeout=3) as sock:
            sock.sendall(f'GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nHost: 127.0.0.1:{self.port}\r\n\r\n'.encode())
            self.assertIn(b' 400 ',sock.recv(1024).split(b'\r\n')[0])
        for _ in range(2):self.assertEqual(self.send('/api/skills/sample/dirs','POST',{'path':'refs'},token)[0],201)
        path='/api/skills/sample/files/refs/%E4%BE%8B.txt'
        for content in ('first 世界','updated 世界'):
            self.assertEqual(self.send(path,'PUT',{'content':content},token)[0],200)
            self.assertEqual(json.loads(self.send(path,token=token)[1])['content'],content)
        status,body,headers=self.send(path,token=token)
        hs,empty,hh=self.send(path,'HEAD',token=token)
        self.assertEqual((hs,empty,hh['Content-Length']),(status,b'',str(len(body))))
        outside=self.f.path/'outside';outside.write_text('untouched')
        (self.f.skill/'alias').symlink_to(outside)
        self.assertNotEqual(self.send('/api/skills/sample/files/alias','PUT',{'content':'bad'},token)[0],200)
        self.assertEqual(outside.read_text(),'untouched')
        self.assertTrue(instance.stop(3).stopped)

    def test_actual_fsync_unknown_quarantines_http_and_retains_lease(self):
        instance=self.start();token=self.login();original=os.fsync
        def fail_after_rename(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('synthetic private failure')
            return original(fd)
        with patch('artex.tools.filesystem.os.fsync',side_effect=fail_after_rename):
            status,body,_=self.send('/api/skills/sample/files/new','PUT',{'content':'written'},token)
        self.assertEqual(status,500);self.assertEqual(json.loads(body)['outcome'],'unknown')
        self.assertNotIn(b'synthetic private failure',body)
        self.assertEqual((self.f.skill/'new').read_text(),'written')
        self.assertEqual(self.send('/api/skills',token=token)[0],503)
        self.assertEqual(instance.stop(3).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_root_revocation_denies_http_and_refuses_clean_release(self):
        instance=self.start();token=self.login()
        self.f.root.chmod(0o755)
        self.assertEqual(self.send('/api/skills',token=token)[0],503)
        self.assertEqual(instance.stop(3).reason,'ownership_unresolved')
        self.assertIsNotNone(instance._lease._fd)
        self.f.root.chmod(0o700)
        self.assertTrue(instance.stop(3).stopped)  # Explicit post-reconciliation observation.

    def test_actual_cli_signal_drain_and_no_credential_output(self):
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONPATH':os.pathsep.join(sys.path),
             'HOME':str(self.f.path),'TMPDIR':str(self.f.path),'PYTHONNOUSERSITE':'1','PYTHONUNBUFFERED':'1'}
        command=[sys.executable,'-m','artex.skills_workspace','--root',str(self.f.root),
                 '--password-file',str(self.f.password),'--exclusive-owner','--enable-writes','--port',str(self.port)]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        try:
            ready,_,_=select.select([child.stdout],[],[],8)
            self.assertTrue(ready,'bounded launcher readiness timeout')
            line=child.stdout.readline();self.assertIn(b'Skills workspace ready',line)
            token=self.login()
            self.assertEqual(self.send('/api/skills/sample/files/child','PUT',{'content':'inert'},token)[0],200)
            os.kill(child.pid,signal.SIGTERM)
            stdout,stderr=child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,stderr.decode())
            self.assertNotIn(b'synthetic-test-password',line+stdout+stderr)
            self.assertNotIn(token.encode(),line+stdout+stderr)
            lease=RuntimeLease(str(self.f.root/'.artex-skills.lock'));self.assertTrue(lease.acquire());lease.release()
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGKILL);child.communicate(timeout=5)


if __name__=='__main__':unittest.main()
