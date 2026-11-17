"""New-skill integration through actual authenticated loopback and child CLI.

Synthetic private files/password only. No database, provider, execution or browser.
Unknown-case fixture cleanup is not production reconciliation or clean release.
"""
import http.client
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
from artex.skills import parse_skill
import test_skills_workspace_transport as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.t = fixtures.Tests(); self.t.setUp(); self.addCleanup(self.t.doCleanups)
        self.root = self.t.f.root

    def create(self, name, token=None, **values):
        # Send actual UTF-8, not only JSON's ASCII escape representation.
        body = json.dumps({'name':name,'description':'Synthetic 世界',**values}, ensure_ascii=False).encode('utf-8')
        conn = http.client.HTTPConnection('127.0.0.1', self.t.port, timeout=4)
        headers = {'Content-Type':'application/json'}
        if token: headers['Authorization'] = 'Bearer '+token
        try:
            conn.request('POST','/api/skills',body,headers)
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally: conn.close()

    def test_actual_create_catalog_content_head_conflict_and_transport_refusals(self):
        (self.t.f.skill/'SKILL.md').unlink(); self.t.f.skill.rmdir()
        instance = self.t.start()
        name = '資料'; target = self.root/name
        self.assertEqual(self.create(name)[0], 401); self.assertFalse(target.exists())
        token = self.t.login()
        self.assertEqual(json.loads(self.t.send('/api/skills',token=token)[1]),{'skills':[]})
        for headers in ({'Host':'evil.invalid'},{'Origin':'null'},
                        {'Origin':'https://evil.invalid'},
                        {'Host':'evil.invalid','X-Forwarded-Host':f'127.0.0.1:{self.t.port}'}):
            self.assertEqual(self.t.send('/api/skills','POST',{'name':name,'description':'Synthetic'},token,headers)[0],403)
            self.assertFalse(target.exists())
        status, body, _ = self.create(name,token,instructions='# Inert 世界\nNever executed.\n')
        self.assertEqual(status,201); self.assertEqual(json.loads(body),{'name':name})
        path = '/api/skills/'+quote(name,safe='')+'/files/SKILL.md'
        status, content_body, _ = self.t.send(path,token=token)
        self.assertEqual(status,200)
        content = json.loads(content_body)['content']
        self.assertEqual((target/'SKILL.md').read_text(encoding='utf-8'),content)
        parsed = parse_skill(content)
        self.assertEqual((parsed.name,parsed.description),(name,'Synthetic 世界'))
        self.assertIn('Never executed.',parsed.instructions)
        status, empty, headers = self.t.send(path,'HEAD',token=token)
        self.assertEqual((status,empty,headers['Content-Length']),(200,b'',str(len(content_body))))
        status, catalog, _ = self.t.send('/api/skills',token=token)
        self.assertEqual(status,200)
        row = next(row for row in json.loads(catalog)['skills'] if row['name']==name)
        self.assertIn('SKILL.md',row['files'])
        before = (target/'SKILL.md').read_bytes()
        self.assertEqual(self.create(name,token,description='must not replace')[0],409)
        self.assertEqual((target/'SKILL.md').read_bytes(),before)
        self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop(3).stopped)
        lease = RuntimeLease(instance._lease.path)
        self.assertTrue(lease.acquire()); lease.release()

    def test_actual_read_only_profile_refuses_creation_without_effect_or_quarantine(self):
        self.t.config = host.WorkspaceConfig(str(self.root),str(self.t.f.password),True,False,self.t.port)
        instance = self.t.start(); token = self.t.login()
        self.assertEqual(self.create('denied',token)[0],403)
        self.assertFalse((self.root/'denied').exists())
        self.assertFalse(instance._unknown)
        self.assertEqual(self.t.send('/api/skills',token=token)[0],200)
        self.assertTrue(instance.stop(3).stopped)

    def test_actual_post_effect_fsync_failure_returns_unknown_and_retains_lease(self):
        instance = self.t.start(); token = self.t.login()
        root_identity = (os.stat(self.root).st_dev,os.stat(self.root).st_ino)
        original = os.fsync
        def fault(fd):
            current = os.fstat(fd)
            if (current.st_dev,current.st_ino)==root_identity:
                raise OSError('synthetic-private-root-fsync')
            return original(fd)
        with patch('os.fsync',side_effect=fault):
            status, body, _ = self.create('partial',token)
        self.assertEqual(status,500)
        self.assertEqual((json.loads(body)['outcome'],json.loads(body)['retry']),('unknown',False))
        self.assertNotIn(b'synthetic-private-root-fsync',body)
        self.assertTrue((self.root/'partial').is_dir())
        self.assertEqual(self.t.send('/api/skills',token=token)[0],503)
        self.assertEqual(instance.stop(3).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable): RuntimeLease(instance._lease.path).acquire()

    def test_actual_disconnect_after_creation_retains_unacknowledged_mutation(self):
        instance = self.t.start(); token = self.t.login()
        entered = threading.Event(); release = threading.Event()
        identity = (os.stat(self.root).st_dev,os.stat(self.root).st_ino)
        original = os.fsync
        def hold(fd):
            current = os.fstat(fd)
            if (current.st_dev,current.st_ino)==identity:
                entered.set()
                if not release.wait(5): raise OSError('synthetic fixture release timeout')
            return original(fd)
        payload = json.dumps({'name':'unacknowledged','description':'Synthetic'}).encode()
        client = socket.create_connection(('127.0.0.1',self.t.port),timeout=3)
        try:
            with patch('os.fsync',side_effect=hold):
                header = (f'POST /api/skills HTTP/1.1\r\nHost: 127.0.0.1:{self.t.port}\r\n'
                          f'Authorization: Bearer {token}\r\nContent-Type: application/json\r\n'
                          f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n').encode()
                client.sendall(header+payload)
                self.assertTrue(entered.wait(3),'Creation did not reach owned-root durability boundary')
                # Reset only this synthetic client connection, before the server
                # can acknowledge the already-created directory/file.
                client.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
                client.close(); release.set()
                with instance._state:
                    self.assertTrue(instance._state.wait_for(lambda:not instance._active,4))
            self.assertTrue((self.root/'unacknowledged').is_dir())
            self.assertTrue(instance._unknown)
            self.assertEqual(instance.stop(3).reason,'mutation_unknown')
            with self.assertRaises(LeaseUnavailable): RuntimeLease(instance._lease.path).acquire()
        finally:
            release.set(); client.close()

    def test_actual_child_cli_create_then_natural_signal_drain(self):
        env = {'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONPATH':os.pathsep.join(sys.path),
               'HOME':str(self.t.f.path),'TMPDIR':str(self.t.f.path),
               'PYTHONNOUSERSITE':'1','PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
        command = [sys.executable,'-m','artex.skills_workspace','--root',str(self.root),
                   '--password-file',str(self.t.f.password),'--exclusive-owner','--enable-writes',
                   '--port',str(self.t.port)]
        child = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        try:
            ready,_,_ = select.select([child.stdout],[],[],8)
            self.assertTrue(ready,'bounded launcher readiness timeout')
            line = child.stdout.readline(); self.assertIn(b'Skills workspace ready',line)
            token = self.t.login()
            status, body, _ = self.create('from-cli',token)
            self.assertEqual((status,json.loads(body)),(201,{'name':'from-cli'}))
            self.assertEqual(self.create('from-cli',token)[0],409)
            os.kill(child.pid,signal.SIGTERM)
            stdout,stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,stderr.decode())
            self.assertNotIn(b'synthetic-test-password',line+stdout+stderr)
            self.assertNotIn(token.encode(),line+stdout+stderr)
            self.assertEqual(parse_skill((self.root/'from-cli'/'SKILL.md').read_text()).name,'from-cli')
            lease = RuntimeLease(str(self.root/'.artex-skills.lock'))
            self.assertTrue(lease.acquire()); lease.release()
        finally:
            if child.poll() is None:
                # Failure-only fixture containment, never counted as a passing
                # natural exit or production-safe unknown-outcome cleanup.
                os.killpg(child.pid,signal.SIGKILL); child.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
