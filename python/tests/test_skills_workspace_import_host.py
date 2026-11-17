"""Explicit archive-import mode over synthetic private trees only.

Native HTTP/CLI evidence is separate from injected syscall/acknowledgement faults.
Unknown-fixture teardown is not production recovery, rollback or clean drain.
"""
import http.client
import io
import json
import os
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import quote

from artex import skills_workspace as host
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.auth import sign_jwt
from artex.skills import parse_skill
import test_skills_workspace_host as fixtures
import test_skills_workspace_transport as transport


def archive(name='資料', extra=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('SKILL.md',f'---\nname: {name}\ndescription: Synthetic import 世界\n---\n# Inert only\n')
        for path,data in (extra or {'refs/例.txt':b'Synthetic reference'}).items():z.writestr(path,data)
    return output.getvalue()


def multipart(data,filename='synthetic.zip'):
    boundary='SyntheticImportBoundary';content_type='multipart/form-data; boundary='+boundary
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
          'Content-Type: application/zip\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
    return content_type,body


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_import_mode_is_exact_opt_in_requiring_write_mode_before_lease(self):
        for value,writes in ((True,False),(1,True),('yes',True),(None,True)):
            config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,writes,enable_imports=value)
            with patch.object(host.RuntimeLease,'acquire') as acquire:
                with self.assertRaises(ValueError):host.SkillsWorkspaceHost.prepare(config)
                acquire.assert_not_called()
        with patch.object(host.SkillsWorkspaceHost,'prepare') as prepare,patch('sys.stderr',new=io.StringIO()):
            self.assertEqual(host.main(['--root',str(self.f.root),'--password-file',str(self.f.password),
                '--exclusive-owner','--enable-imports']),1)
            prepare.assert_not_called()

    def test_profile_optional_import_and_delete_flags_are_independent(self):
        for imports,deletes in ((False,False),(False,True),(True,False),(True,True)):
            config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,
                enable_imports=imports,enable_deletes=deletes)
            instance=self.f.prepare(config)
            status,body,headers=self.f.request(instance,'/api/skills-workspace')
            expected={'profile':'skills-workspace','writes_enabled':True}
            if deletes:expected['deletes_enabled']=True
            if imports:expected['imports_enabled']=True
            self.assertEqual((status,json.loads(body)),(200,expected))
            self.assertEqual(headers['Cache-Control'],'no-store')
            hs,empty,hh=self.f.request(instance,'/api/skills-workspace','HEAD')
            self.assertEqual((hs,empty,hh['Content-Length']),(200,b'',str(len(body))))
            self.assertTrue(instance.stop().stopped)

    def test_post_header_import_mode_drift_withholds_hint(self):
        config=host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,enable_imports=True)
        instance=self.f.prepare(config)
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/skills-workspace','CONTENT_LENGTH':'0',
             'wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        statuses=[]
        def start(status,headers):statuses.append(status);object.__setattr__(config,'enable_imports',False)
        try:
            self.assertEqual(b''.join(instance.application(env,start)),b'')
            self.assertEqual(statuses,['200 OK']);self.assertFalse(instance._unknown)
        finally:object.__setattr__(config,'enable_imports',True)
        self.assertTrue(instance.stop().stopped)

    def test_upload_response_tail_is_accounted_as_root_mutation(self):
        instance=self.f.prepare()
        def app(env,start):start('201 Created',[]);return [b'{"name":"synthetic","files":1}']
        instance._application=app
        body=instance.application({'REQUEST_METHOD':'POST','PATH_INFO':'/api/skills/upload',
            'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()},lambda *a:None)
        next(body);body.close()
        self.assertTrue(instance._unknown);self.assertEqual(instance.stop(0).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_actual_post_header_authority_loss_retains_installed_effect(self):
        instance=self.f.prepare(host.WorkspaceConfig(str(self.f.root),str(self.f.password),True,True,enable_imports=True))
        content_type,data=multipart(archive('installed'))
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/skills/upload','CONTENT_TYPE':content_type,
             'CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data),
             'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        statuses=[];original=instance._auth.key
        def start(status,headers):statuses.append(status);instance._auth.key=b'withdrawn-synthetic-authority-key'
        try:self.assertEqual(b''.join(instance.application(env,start)),b'')
        finally:instance._auth.key=original
        self.assertEqual(statuses,['201 Created']);self.assertTrue((self.f.root/'installed'/'SKILL.md').is_file())
        self.assertEqual(instance.stop(0).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.t.config=host.WorkspaceConfig(str(self.t.f.root),str(self.t.f.password),True,True,
            self.t.port,enable_imports=True)

    def upload(self,data=None,token=None,path='/api/skills/upload',headers=None):
        content_type,body=multipart(archive() if data is None else data)
        conn=http.client.HTTPConnection('127.0.0.1',self.t.port,timeout=5)
        fields={'Content-Type':content_type,**(headers or {})}
        if token:fields['Authorization']='Bearer '+token
        conn.request('POST',path,body,fields)
        response=conn.getresponse();result=response.status,response.read(),dict(response.getheaders());conn.close()
        return result

    def test_actual_authenticated_unicode_import_catalog_read_edit_and_conflict(self):
        instance=self.t.start()
        self.assertEqual(self.upload()[0],401);self.assertFalse((self.t.f.root/'資料').exists())
        token=self.t.login()
        data=archive(extra={'refs/例.txt':b'Synthetic reference','assets/raw.bin':b'\x00\xff\x10'})
        status,body,_=self.upload(data,token=token)
        self.assertEqual((status,json.loads(body)),(201,{'name':'資料','files':3}))
        target=self.t.f.root/'資料';self.assertEqual(parse_skill((target/'SKILL.md').read_text()).name,'資料')
        self.assertEqual((target/'refs'/'例.txt').read_bytes(),b'Synthetic reference')
        self.assertEqual((target/'assets'/'raw.bin').read_bytes(),b'\x00\xff\x10')
        self.assertEqual(stat.S_IMODE((target/'assets'/'raw.bin').stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o700)
        rows=json.loads(self.t.send('/api/skills',token=token)[1])['skills']
        self.assertIn('資料',[row['name'] for row in rows])
        path='/api/skills/'+quote('資料')+'/files/refs/'+quote('例.txt')
        self.assertEqual(json.loads(self.t.send(path,token=token)[1])['content'],'Synthetic reference')
        self.assertEqual(self.t.send(path,'PUT',{'content':'Edited after import 世界'},token)[0],200)
        self.assertEqual(json.loads(self.t.send(path,token=token)[1])['content'],'Edited after import 世界')
        self.assertEqual(self.upload(token=token)[0],409)
        self.assertEqual(self.upload(token=token,path='/api/skills/upload?overwrite=true')[0],400)
        self.assertEqual((target/'refs'/'例.txt').read_text(),'Edited after import 世界')
        self.assertFalse(instance._unknown);self.assertTrue(instance.stop(3).stopped)
        lease=RuntimeLease(instance._lease.path);self.assertTrue(lease.acquire());lease.release()

    def test_write_and_delete_modes_never_imply_import_grant(self):
        self.t.config=host.WorkspaceConfig(str(self.t.f.root),str(self.t.f.password),True,True,
            self.t.port,enable_deletes=True)
        instance=self.t.start();token=self.t.login()
        self.assertEqual(self.upload(token=token)[0],403);self.assertFalse((self.t.f.root/'資料').exists())
        mode=json.loads(self.t.send('/api/skills-workspace',token=token)[1])
        self.assertEqual(mode,{'profile':'skills-workspace','writes_enabled':True,'deletes_enabled':True})
        self.assertFalse(instance._unknown);self.assertTrue(instance.stop(3).stopped)

    def test_invalid_archives_transport_and_origin_refusals_leave_no_effects(self):
        instance=self.t.start();token=self.t.login()
        self.assertEqual(self.upload(b'not a zip',token)[0],400)
        # Header-only oversize probe: the bounded server rejects before body
        # admission, rather than requiring a client to finish an invalid upload.
        connection=http.client.HTTPConnection('127.0.0.1',self.t.port,timeout=4)
        try:
            connection.request('POST','/api/skills/upload',b'',{
                'Content-Length':str(host.MAX_JSON_BYTES+1),'Content-Type':'application/zip',
                'Authorization':'Bearer '+token})
            response=connection.getresponse();self.assertEqual(response.status,413);response.read()
        finally:connection.close()
        for headers in ({'Host':'evil.invalid'},{'Origin':'null'},{'Origin':'https://evil.invalid'},
                        {'Host':'evil.invalid','X-Forwarded-Host':f'127.0.0.1:{self.t.port}'}):
            self.assertEqual(self.upload(token=token,headers=headers)[0],403)
        self.assertEqual(sorted(p.name for p in self.t.f.root.iterdir()),['.artex-skills.lock','sample'])
        self.assertFalse(instance._unknown);self.assertTrue(instance.stop(3).stopped)

    def test_actual_partial_file_write_failure_is_unknown_without_recursive_cleanup(self):
        instance=self.t.start();token=self.t.login();original=os.write;calls=[]
        def partial(fd,data):
            if not calls:
                calls.append(fd);return original(fd,data[:7])
            raise OSError('Synthetic private interrupted import write')
        with patch('os.write',side_effect=partial):status,body,_=self.upload(archive('partial'),token)
        self.assertEqual(status,500);self.assertEqual(json.loads(body)['outcome'],'unknown')
        self.assertEqual(json.loads(body)['retry'],False);self.assertNotIn(b'Synthetic private',body)
        target=self.t.f.root/'partial';self.assertTrue(target.is_dir())
        files=[p for p in target.rglob('*') if p.is_file()];self.assertTrue(files)
        self.assertEqual(files[0].stat().st_size,7)
        self.assertEqual(self.t.send('/api/skills-workspace',token=token)[0],503)
        self.assertEqual(instance.stop(3).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_actual_root_fsync_failure_preserves_complete_tree_but_not_success(self):
        instance=self.t.start();token=self.t.login();original=os.fsync
        info=self.t.f.root.stat();identity=(info.st_dev,info.st_ino)
        def fail(fd):
            st=os.fstat(fd)
            if (st.st_dev,st.st_ino)==identity:raise OSError('Synthetic private import durability')
            return original(fd)
        with patch('os.fsync',side_effect=fail):status,body,_=self.upload(archive('durability'),token)
        self.assertEqual(status,500);self.assertEqual(json.loads(body)['outcome'],'unknown')
        self.assertTrue((self.t.f.root/'durability'/'SKILL.md').is_file())
        self.assertEqual(instance.stop(3).reason,'mutation_unknown')
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_actual_disconnect_after_files_preserves_unacknowledged_import(self):
        instance=self.t.start();token=self.t.login();original=os.fsync
        info=self.t.f.root.stat();identity=(info.st_dev,info.st_ino);entered=threading.Event();release=threading.Event()
        def hold(fd):
            st=os.fstat(fd)
            if (st.st_dev,st.st_ino)==identity:
                entered.set()
                if not release.wait(5):raise OSError('Synthetic bounded fixture timeout')
            return original(fd)
        content_type,data=multipart(archive('unacknowledged'))
        client=socket.create_connection(('127.0.0.1',self.t.port),timeout=3)
        try:
            with patch('os.fsync',side_effect=hold):
                request=(f'POST /api/skills/upload HTTP/1.1\r\nHost: 127.0.0.1:{self.t.port}\r\n'
                    f'Authorization: Bearer {token}\r\nContent-Type: {content_type}\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n').encode()+data
                client.sendall(request);self.assertTrue(entered.wait(3))
                client.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0));client.close();release.set()
                with instance._state:self.assertTrue(instance._state.wait_for(lambda:not instance._active,4))
            self.assertTrue((self.t.f.root/'unacknowledged'/'SKILL.md').is_file())
            self.assertEqual(instance.stop(3).reason,'mutation_unknown')
            with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()
        finally:release.set();client.close()

    def test_actual_explicit_import_cli_natural_shutdown_and_lease_reacquire(self):
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONPATH':os.pathsep.join(sys.path),
            'HOME':str(self.t.f.path),'TMPDIR':str(self.t.f.path),'PYTHONNOUSERSITE':'1',
            'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
        command=[sys.executable,'-m','artex.skills_workspace','--root',str(self.t.f.root),
            '--password-file',str(self.t.f.password),'--exclusive-owner','--enable-writes',
            '--enable-imports','--port',str(self.t.port)]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        try:
            ready,_,_=select.select([child.stdout],[],[],8);self.assertTrue(ready)
            line=child.stdout.readline();self.assertIn(b'Skills workspace ready',line)
            token=self.t.login();status,body,_=self.upload(archive('cli-import'),token)
            self.assertEqual((status,json.loads(body)),(201,{'name':'cli-import','files':2}))
            os.kill(child.pid,signal.SIGTERM);out,err=child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,err.decode())
            self.assertNotIn(token.encode(),line+out+err);self.assertNotIn(b'synthetic-test-password',line+out+err)
            lease=RuntimeLease(str(self.t.f.root/'.artex-skills.lock'));self.assertTrue(lease.acquire());lease.release()
        finally:
            # Failure containment of the synthetic child is never passing drain.
            if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.communicate(timeout=5)


if __name__=='__main__':unittest.main()
