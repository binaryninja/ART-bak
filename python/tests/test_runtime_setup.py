"""Opt-in assembly with real stores and fake SQL/transport; no sockets or keys."""
from contextlib import contextmanager
import io
import json
import re
import unittest
from artex.server.runtime_setup import build_profile_runtime_application
from artex.health import Registry
from test_application import Executor, TestCodec


class Database(Executor):
    def __init__(self):
        super().__init__();self.profiles={};self.fail_pool=False
    @contextmanager
    def transaction(self):
        from copy import deepcopy
        before=deepcopy(self.profiles)
        try:yield self
        except BaseException:self.profiles=before;raise
    def fetch_one(self,q):
        self.calls.append(q)
        assert q.sql.startswith('INSERT INTO llm_profiles(')
        names=q.sql.split('(',1)[1].split(')',1)[0].split(',')
        row=dict(zip(names,q.params));identifier=len(self.profiles)+1
        row.update(id=identifier,is_default=False);self.profiles[identifier]=row
        return (identifier,)
    def execute(self,q):
        if q.sql.startswith('UPDATE llm_profiles'):
            self.calls.append(q)
            if 'is_default=false' in q.sql:
                for p in self.profiles.values():p['is_default']=False
                return len(self.profiles)
            if 'is_default=true' in q.sql:
                p=self.profiles.get(q.params[0])
                if p is None:return 0
                p['is_default']=True;return 1
            names=re.split(r',(?=[a-z_]+=)',q.sql.split(' SET ')[1].split(' WHERE ')[0])
            p=self.profiles[q.params[-1]]
            for part,value in zip(names,q.params):p[part.split('=')[0]]=value
            return 1
        return super().execute(q)
    def fetch_all(self,q):
        if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:return []
        if 'FROM llm_profiles' not in q.sql:return super().fetch_all(q)
        self.calls.append(q)
        if self.fail_pool and 'pool_exclude)' in q.sql:raise RuntimeError('fixture secret storage detail')
        rows=list(self.profiles.values())
        if 'WHERE id=$1' in q.sql:rows=[p for p in rows if p['id']==q.params[0]]
        elif 'WHERE is_default LIMIT' in q.sql:rows=[p for p in rows if p['is_default']]
        elif 'pool_exclude)' in q.sql:rows=[p for p in rows if p.get('api_key') and (p['is_default'] or not p['pool_exclude'])]
        names=['id','name','format','base_url','proxy','model','api_key','rate_per_second','rate_per_minute','context_window_k','reasoning_effort','is_default','priority','pool_exclude','thinking_type','streaming','max_tokens','max_tokens_field','session_header_key','retry_connect_attempts','retry_connect_interval_ms','retry_empty_attempts','retry_empty_interval_ms','retry_stream_attempts','retry_stream_interval_ms']
        if "COALESCE(api_key,'')" not in q.sql:names[6]='api_key_hint'
        return [tuple(p.get(n,0 if n.startswith('retry_') else '') for n in names) for p in rows]


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.db=Database();self.constructions=[];self.transport_calls=[]
        def factory(config):
            self.constructions.append(config.model)
            def transport(*args):self.transport_calls.append(args);raise AssertionError('no provider calls')
            return transport
        self.factory=factory
        self.bundle=build_profile_runtime_application(self.db,b'x'*32,TestCodec(),transport_factory=factory,health=Registry())
    def request(self,path,method='GET',data=None,token=None):
        raw=json.dumps(data).encode() if data is not None else b''
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+token
        statuses=[];body=b''.join(self.bundle.application(env,lambda s,h:statuses.append(s)))
        return int(statuses[0].split()[0]),json.loads(body)
    def token(self):return self.request('/api/auth/init','POST',{'password':'dummy-password'})[1]['token']
    def test_auth_save_activate_snapshot_no_provider_calls(self):
        self.assertEqual(self.constructions,[])
        self.assertEqual(self.request('/api/llm')[0],401)
        token=self.token()
        self.assertFalse(self.request('/api/llm',token=token)[1]['configured'])
        status,payload=self.request('/api/llm/profiles','POST',{'name':'fixture','model':'dummy','base_url':'https://example.test/v1','api_key':'dummy-fixture-key'},token)
        self.assertEqual((status,payload),(200,{'id':1}))
        self.assertFalse(self.request('/api/llm',token=token)[1]['configured'])
        self.assertEqual(self.request('/api/llm/profiles/active','POST',{'id':1},token),(200,{'ok':True}))
        status,snapshot=self.request('/api/llm',token=token)
        self.assertEqual(status,200);self.assertTrue(snapshot['configured']);self.assertEqual(snapshot['model'],'dummy')
        self.assertNotIn('dummy-fixture-key',json.dumps(snapshot))
        self.assertIsNotNone(self.bundle.profile_coordinator.runtime()[0])
        self.assertEqual(self.transport_calls,[])
    def test_blank_key_update_preserves_key_and_explicit_restart_reloads(self):
        token=self.token()
        self.assertEqual(self.request('/api/llm/profiles','POST',{'name':'fixture','model':'dummy','api_key':'dummy-fixture-key'},token)[0],200)
        self.assertEqual(self.request('/api/llm/profiles/active','POST',{'id':1},token)[0],200)
        self.assertEqual(self.request('/api/llm/profiles','POST',{'id':1,'name':'fixture','model':'changed','api_key':''},token)[0],200)
        self.assertEqual(self.db.profiles[1]['api_key'],'dummy-fixture-key')
        self.assertEqual(self.request('/api/llm',token=token)[1]['model'],'changed')
        self.bundle=build_profile_runtime_application(self.db,b'x'*32,TestCodec(),transport_factory=self.factory,health=Registry())
        self.assertEqual(self.request('/api/llm',token=token)[1]['model'],'changed')
        self.assertEqual(self.transport_calls,[])

    def test_apply_failure_truthfully_reports_persisted(self):
        token=self.token();self.db.fail_pool=True
        status,result=self.request('/api/llm/profiles','POST',{'name':'fixture','model':'dummy','api_key':'dummy-key'},token)
        self.assertEqual(status,503);self.assertTrue(result['persisted']);self.assertFalse(result['runtime_applied'])
        self.assertEqual(len(self.db.profiles),1)
        self.assertFalse(self.bundle.profile_coordinator.snapshot()['configured'])
        self.assertEqual(self.transport_calls,[])
    def test_invalid_dependency_or_override_before_reads(self):
        for kwargs in ({'health':None},{'transport_factory':None},{'application_options':{'runtime_snapshot':lambda:{}}},{'application_options':{'unknown':True}}):
            params=dict(transport_factory=self.factory,health=Registry());params.update(kwargs)
            db=Database()
            with self.assertRaises((ValueError,TypeError)):
                build_profile_runtime_application(db,b'x'*32,TestCodec(),**params)
            self.assertEqual(db.calls,[])
    def test_startup_read_failure_not_hidden(self):
        db=Database();db.fail_pool=True
        with self.assertRaises(Exception):build_profile_runtime_application(db,b'x'*32,TestCodec(),transport_factory=self.factory,health=Registry())
        self.assertEqual(self.transport_calls,[])


if __name__=='__main__':unittest.main()
