"""Independent dynamic router→builder→fixed activity stream contract tests."""
import io,json,unittest
from unittest.mock import patch
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from artex.db.tasks import TaskStore
from artex.server.application import build_application
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.auth import sign_jwt
from test_application import TestCodec
from test_task_activity_read import DB as ActivityDB

class DB(ActivityDB):
    def __init__(self):super().__init__();self.exploration=9;self.parent=True;self.status='running';self.page_reads=0
    def fetch_all(self,q):
        if q.sql.startswith('SELECT exploration_id FROM tasks'):
            self.queries.append(q)
            return [(self.exploration,)] if self.parent and (len(q.params)==1 or q.params[1]==self.exploration) else []
        if 'sized AS' in q.sql:self.page_reads+=1
        return super().fetch_all(q)
class Review(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.selector=True;self.raw=True;self.live=True;self.allow_later=True;self.selections=[]
        tasks=TaskStore(self.db)
        tasks.get=lambda id:SimpleNamespace(id=id,exploration_id=self.db.exploration,status=self.db.status)
        def request_grant(req,scope,selection):
            self.selections.append(selection)
            return selection.since==0 or self.allow_later
        builder=TaskReadBuilder(self.db,request_authorize=lambda *a:True,context_authorize=lambda *a:True,
            asset_authorize=lambda *a:True,runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:True,
            activity_raw_authorize=lambda *a:self.raw,activity_request_authorize=request_grant,
            stream_authorize=lambda *a:True,max_rows=2)
        self.router=TaskRuntimeReadRouter(tasks,builder,revision='r1',authorize_selector=lambda *a:self.selector,
            current=lambda scope:self.live,lifecycle_guard=lambda scope:nullcontext())
        self.base=build_application(self.db,b'x'*32,TestCodec())
        self.app=self.router.bind(self.base)
    def start(self,path='/api/exploration/activity/stream',query='task=5',headers=None):
        statuses=[]
        env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'wsgi.input':io.BytesIO(),
            'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
        env.update(headers or {})
        result=self.app(env,lambda status,h:statuses.append(status))
        self.assertEqual(statuses[0].split()[0],'200')
        return result
    def test_dispatch_to_iteration_selector_revocation_no_activity_sql(self):
        stream=self.start();self.selector=False
        self.assertEqual(list(stream),[]);self.assertEqual(self.db.page_reads,0)
        self.assertTrue(stream.failed)
    def test_raw_grant_revocation_after_dispatch_and_buffered_resume(self):
        stream=self.start();self.raw=False
        self.assertEqual(list(stream),[]);self.assertEqual(self.db.page_reads,0)
        self.raw=True;stream=self.start();self.assertIn(b'private-1',next(stream));self.raw=False
        self.assertEqual(list(stream),[]);self.assertEqual(self.db.page_reads,1)
    def test_old_stream_never_retargets_changed_exploration(self):
        stream=self.start();self.assertIn(b'private-1',next(stream));self.db.exploration=44
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed)
        self.assertEqual(self.db.page_reads,1)
    def test_later_batch_exact_selector_request_denial_before_sql(self):
        stream=self.start();next(stream);next(stream);self.allow_later=False
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed)
        self.assertEqual(self.db.page_reads,1)
        self.assertTrue(any(s.since==2 for s in self.selections))
    def test_completed_task_history_read_is_not_execution_gate(self):
        self.db.status='done'
        body=b''.join(self.start('/api/exploration/activity/history','task=5&session=plan&limit=2'))
        value=json.loads(body);self.assertEqual(value['snapshot_cursor'],5)
        self.assertEqual([v['seq'] for v in value['items']],[4,5])
    def test_reconnect_header_preserved_and_close_before_iteration_inert(self):
        stream=self.start(query='task=5&since=0',headers={'HTTP_LAST_EVENT_ID':'3'})
        self.assertEqual([int(frame.split(b'\n')[0][4:]) for frame in stream],[4,5])
        before=self.db.page_reads
        stream=self.start();stream.close();stream.close();self.assertEqual(list(stream),[])
        self.assertEqual(self.db.page_reads,before)

    def test_original_jwt_authorizer_swap_after_dispatch_stops_stream(self):
        stream=self.start();self.base.router.authorize=lambda request:True
        self.assertEqual(list(stream),[])
        self.assertEqual(self.db.page_reads,0)
        self.assertTrue(stream.failed)

    def test_original_jwt_authorizer_swap_during_first_frame_stops_disclosure(self):
        from artex.server import task_activity
        original=task_activity.sse_frame
        def frame(*args,**kwargs):
            value=original(*args,**kwargs)
            self.base.router.authorize=lambda request:True
            return value
        stream=self.start()
        with patch.object(task_activity,'sse_frame',frame):
            self.assertEqual(list(stream),[])
        self.assertEqual(self.db.page_reads,1)
        self.assertTrue(stream.failed)
