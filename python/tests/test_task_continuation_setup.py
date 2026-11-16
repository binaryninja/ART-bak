"""Existing JWT task endpoint, actual same-process lifecycle, inert providers."""
import unittest
from artex.server.task_continuation_setup import enable_task_continuation
from artex.agent.task_runtime import TaskRuntimeAssembly
import test_task_runtime_application as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        old=self.f.runtime
        self.f.runtime=TaskRuntimeAssembly(old.executor,providers=old.providers,transcript=old.transcript,prompts=old._prompts,policy_revision=old._revision,current=old._current,lifecycle_guard=old._guard,authorize=old._authorize,asset_authorize=old._assets,validate_input=old._input,graph_catalog=old._catalog,cleanup_authorize=lambda p:True)
        self.allowed=True;self.grants=[];self.enabled=None;self.capacity=2
        original=self.f.build
        def build(**kw):
            kw.setdefault('capacity',self.capacity)
            app=original(**kw)
            def authorize(p):self.grants.append(p.action);return self.allowed
            self.enabled=enable_task_continuation(app,authorize=authorize)
            return app
        self.f.build=build
    def prepare(self):
        self.f.test_jwt_create_then_explicit_tick_uses_actual_goals_transport()
        self.app=self.f.bundle;self.handle=self.app.runner.handles()[0]
        from datetime import datetime,timezone
        stamp=datetime(2026,1,1,tzinfo=timezone.utc);db=self.f.f.db;old=db.fetch_all
        def query(q):
            if q.sql.startswith('SELECT updated_at FROM tasks'):return [(stamp,)] if db.row[8] is q.params[2] else []
            if q.sql.startswith('SELECT id FROM exploration_nodes WHERE exploration_id=') and "state='running'" in q.sql:return []
            if q.sql.startswith("UPDATE tasks SET paused=false,queued=false,queued_at=NULL,queue_mode=''"):
                row=list(db.row)
                if not row[8] or q.params[2]!=stamp:return []
                row[8]=False;db.row=tuple(row);return [(5,)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=self.handle._receipt
                return [(5,)] if q.params==(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision) else []
            return old(q)
        db.fetch_all=query
    def test_existing_jwt_pause_resume_no_new_route_or_goals(self):
        self.prepare();routes=tuple(self.app.bootstrap.app.router._routes);before=list(self.f.p.calls)
        self.assertEqual(self.f.request('/api/tasks/5/control',{'action':'pause'},False)[0],401)
        self.assertFalse(self.handle.token.cancelled)
        status,payload=self.f.request('/api/tasks/5/control',{'action':'pause'});self.assertEqual(status,200,payload)
        status,payload=self.f.request('/api/tasks/5/control',{'action':'resume'});self.assertEqual(status,200,payload)
        new=self.app.runner.handles()[0];self.assertIsNot(new,self.handle)
        self.app.tick();self.assertIs(new._receipt,self.handle._receipt);self.assertEqual(self.f.p.calls,before)
        self.assertEqual(tuple(self.app.bootstrap.app.router._routes),routes)
    def test_inert_setup_preserves_exact_bundle_and_once(self):
        app=self.f.build();self.assertIs(self.enabled.application,app);self.assertIs(self.enabled.owner.host,app.host)
        self.assertFalse(self.grants);self.assertFalse(self.f.p.calls)
        with self.assertRaises(ValueError):enable_task_continuation(app,authorize=lambda p:True)
    def test_default_application_unchanged(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups)
        app=f.build();self.assertIsNone(app.manager._continuation_owner)
    def test_existing_active_application_cannot_be_reconfigured(self):
        self.prepare()
        with self.assertRaises(ValueError):enable_task_continuation(self.app,authorize=lambda p:True)
        self.assertIs(self.app.manager._continuation_owner,self.enabled.owner)

    def test_full_capacity_replaces_exact_paused_generation(self):
        self.capacity=1;self.prepare();self.assertEqual(self.f.request('/api/tasks/5/control',{'action':'pause'})[0],200)
        writes=[];db=self.f.f.db;original=db.fetch_all
        def observed(q):
            if q.sql.startswith("UPDATE tasks SET paused=false,queued=false,queued_at=NULL,queue_mode=''"):writes.append(q)
            return original(q)
        db.fetch_all=observed
        status,payload=self.f.request('/api/tasks/5/control',{'action':'resume'})
        self.assertEqual(status,200,payload)
        self.assertNotIn('5',self.app.manager._suspensions)
        self.assertEqual(len(self.app.runner.handles()),1)
        self.assertEqual(len(writes),1)
