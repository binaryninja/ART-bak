"""Real JWT/WSGI assembly, fake stores/SQL, temp transcripts; no network."""
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from artex.server.inspection_setup import build_inspection_application
from artex.server.core import json_response
from artex.db.llm_records import LLMRecordStore, LLMRecord
from artex.db.tool_catalog import ToolCatalogStore, CatalogTool
from artex.health import Registry
from test_chat_setup import ChatDB
from test_application import TestCodec


class Records(LLMRecordStore):
    def __init__(self, executor):
        super().__init__(executor, task_id='task1')
        self.reads = 0
    def list(self, **kwargs):
        self.reads += 1
        return [self.get(7)], 1
    def get(self, identifier):
        self.reads += 1
        return LLMRecord(id=7, task_id='task1', session_id='conv-1', raw_request='private fixture')


class Tools(ToolCatalogStore):
    def __init__(self, executor):
        super().__init__(executor)
        self.reads = 0
        self.created = []
    def list(self):
        self.reads += 1
        return self.created
    def usage_counts(self):
        return {}
    def create(self, tool):
        self.created.append(tool)


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ChatDB()
        self.records = Records(self.db)
        self.tools = Tools(self.db)
        self.policy_calls = []
        self.body_allowed = True
        self.tool_allowed = True
        self.options = dict(transport_factory=lambda config: self.no_transport,
            health=Registry(), transcript_root=self.temp.name,
            authorize=lambda req, conv: True, gate=lambda *a: None, tools=(),
            agent_available=lambda key: key == 'fixture-agent',
            system_prompt=lambda conv: 'Fixture only', single_process=True)
        self.kwargs = dict(chat_options=self.options, record_store_resolver=lambda *a: self.records,
            record_authorize=self.allow_records, record_redact=self.redact,
            tool_store=self.tools, tool_authorize=lambda *a: self.tool_allowed)
        self.bundle = self.build()
        self.addCleanup(lambda: self.bundle.close(cancel=True, timeout=3))
        self.token = self.call('/api/auth/init', 'POST', {'password':'dummy-password'}, auth=False)[1]['token']
    def no_transport(self, *args):
        raise AssertionError('No provider calls permitted')
    def allow_records(self, req, action, selection):
        self.policy_calls.append(action)
        return action != 'record_body' or self.body_allowed
    def redact(self, req, row, bodies):
        if 'raw_request' in row: row['raw_request'] = '[redacted]'
        return row
    def build(self, **updates):
        return build_inspection_application(self.db, b'x'*32, TestCodec(), **dict(self.kwargs, **updates))
    def call(self, target, method='GET', data=None, auth=True):
        path, _, query = target.partition('?')
        raw = json.dumps(data).encode() if data is not None else b''
        env = {'REQUEST_METHOD':method, 'PATH_INFO':path, 'QUERY_STRING':query,
               'CONTENT_LENGTH':str(len(raw)), 'wsgi.input':io.BytesIO(raw)}
        if auth: env['HTTP_AUTHORIZATION'] = 'Bearer '+self.token
        status=[]
        body=b''.join(self.bundle.application(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]), json.loads(body)
    def test_startup_no_inspector_reads_and_shared_authenticated_app(self):
        self.assertEqual(self.records.reads,0); self.assertEqual(self.tools.reads,0)
        for path in ('/api/tools','/api/llm/records','/api/conversations','/api/llm'):
            self.assertEqual(self.call(path,auth=False)[0],401)
        self.assertEqual(self.policy_calls,[])
        self.assertIs(self.bundle.application,self.bundle.runtime.application)
        self.assertIs(self.bundle.tools.store,self.tools)
        self.assertEqual(self.call('/api/conversations')[0],200)
        self.assertEqual(self.call('/api/llm')[0],200)
    def test_list_detail_body_permission_and_redaction(self):
        status, body=self.call('/api/llm/records?task=task1')
        self.assertEqual(status,200)
        self.assertNotIn('raw_request',body['records'][0])
        self.assertEqual(self.call('/api/llm/records/7')[1]['raw_request'],'[redacted]')
        self.body_allowed=False
        self.assertEqual(self.call('/api/llm/records/7')[0],403)
        self.assertEqual(self.call('/api/llm/records?task=other')[0],403)
    def test_wrong_record_executor_fails_closed(self):
        self.records=Records(object())
        self.assertEqual(self.call('/api/llm/records')[0],503)
        self.assertEqual(self.records.reads,0)
    def test_tool_metadata_never_registers_executable(self):
        self.assertEqual(self.call('/api/tools')[1],{'tools':[]})
        body=dict(key='fixture',schema={},enabled=True,kind='command',exec={'command':'never executed'})
        self.assertEqual(self.call('/api/tools/custom','POST',body),(200,{'key':'fixture'}))
        self.assertEqual(self.call('/api/tools')[1]['tools'][0]['exec'],body['exec'])
        self.assertEqual(self.call('/api/tools/custom/test','POST',body)[0],503)
        self.tool_allowed=False
        self.assertEqual(self.call('/api/tools/custom','POST',body)[0],403)
        self.assertEqual(len(self.tools.created),1)
    def test_required_policies_rejected_before_runtime_construction(self):
        with patch('artex.server.inspection_setup.build_chat_application') as factory:
            for key in ('record_store_resolver','record_authorize','record_redact','tool_authorize'):
                with self.assertRaises(ValueError): self.build(**{key:None})
            with self.assertRaises(ValueError): self.build(tool_store=Tools(object()))
            factory.assert_not_called()
    def test_duplicate_mount_fails_and_drains_without_partial_routes(self):
        router=self.bundle.application.router
        before=len(router._routes)
        with patch('artex.server.inspection_setup.build_chat_application',return_value=self.bundle.runtime):
            with self.assertRaisesRegex(ValueError,'already registered'): self.build()
        self.assertEqual(len(router._routes),before)
        self.assertTrue(self.bundle.close(timeout=3).drained)
    def test_unsupported_remain_explicit(self):
        self.assertEqual(self.call('/api/tokens/usage')[0],503)
        self.assertEqual(self.call('/api/llm/records','DELETE')[0],503)
        self.assertEqual(self.call('/api/chat','POST',{'message':'no'})[0],404)
