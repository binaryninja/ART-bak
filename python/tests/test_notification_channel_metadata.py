import unittest,json,copy
from contextlib import contextmanager
from artex.db.notification_channel_metadata import ChannelMetadataStore,ChannelMetadataScope
from artex.server.notification_channel_metadata import ChannelMetadataService
from artex.server.core import Request
class Tests(unittest.TestCase):
    def setUp(self):
        self.row={'id':7,'enabled':True,'name':'original','mode':'realtime','rate_per_min':60,'filter':'{}','config':'{"password":"secret","unknown": {"nested":1}}','kind':'email'}
        self.delivery=['pending','sending','sent'];self.allowed=True;self.request_allowed=True;self.queries=[];self.fail_skip=False;self.fail_ack=False;self.before_commit=None
        class DB:
            @contextmanager
            def transaction(db):
                before=copy.deepcopy((self.row,self.delivery))
                try:
                    yield db
                    if self.before_commit:self.before_commit()
                except BaseException:
                    self.row,self.delivery=before;raise
                if self.fail_ack:raise RuntimeError('commit acknowledgement lost')
            def fetch_all(db,q):
                self.queries.append(q);return [] if self.row is None else [(7,self.row['enabled'])]
            def execute(db,q):
                self.queries.append(q)
                if q.sql.startswith('UPDATE notification_channels'):
                    assignments=q.sql.split(' SET ')[1].split(' WHERE ')[0].split(',')
                    for assignment,value in zip(assignments,q.params[1:]):self.row[assignment.split('=')[0]]=value
                    return 1
                if self.fail_skip:raise RuntimeError('inert delivery write failed')
                self.delivery=['skipped' if x in ('pending','sending') else x for x in self.delivery];return 2
        self.store=ChannelMetadataStore(DB(),ChannelMetadataScope('notifications','rev1'),authorize=lambda s,p:self.allowed,current=lambda s:True,lifecycle_guard=lambda s:self.guard())
        self.service=ChannelMetadataService(self.store,authorize=lambda *a:self.request_allowed)
    @contextmanager
    def guard(self):yield
    def request(self,values):return self.service.handle(Request('PATCH','/api/notify/channels/7',body=json.dumps(values).encode()))
    def test_metadata_preserves_configuration_unknowns_exactly(self):
        cfg=self.row['config'];kind=self.row['kind'];result=self.request({'name':' new ','rate_per_min':0,'enabled':False})
        self.assertEqual(result.status,200);self.assertEqual(self.row['name'],'new');self.assertEqual(self.row['rate_per_min'],0)
        self.assertEqual(self.row['config'],cfg);self.assertEqual(self.row['kind'],kind);self.assertEqual(self.delivery,['skipped','skipped','sent'])
        self.assertFalse(any('config' in q.sql or 'kind=' in q.sql for q in self.queries))
    def test_null_omitted_no_effect_and_rejected_config(self):
        before=copy.deepcopy(self.row);self.assertEqual(self.request({'name':None,'enabled':None,'filter':None}).status,200)
        self.assertEqual(self.row,before);self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.queries))
        for body in ({'config':{}},{'kind':None},{'rate_per_min':True},{'filter':{'min_severity':'oops'}}):self.assertEqual(self.request(body).status,400)
    def test_disable_failure_rolls_back_both_but_reports_uncertainty(self):
        self.fail_skip=True;result=self.request({'name':'changed','enabled':False})
        self.assertEqual(result.status,503);self.assertFalse(result.json()['retry']);self.assertEqual(self.row['name'],'original');self.assertTrue(self.row['enabled'])
    def test_commit_ack_lost_no_false_rollback(self):
        self.fail_ack=True;result=self.request({'name':'changed'})
        self.assertEqual(result.status,503);self.assertEqual(result.json()['outcome'],'unknown');self.assertEqual(self.row['name'],'changed')
    def test_original_write_grants_before_sql(self):
        self.request_allowed=False;self.assertEqual(self.request({'enabled':False}).status,403);self.assertFalse(self.queries)
    def test_missing_and_filter(self):
        result=self.request({'filter':{'min_severity':'high','task_ids':[5]}});self.assertEqual(result.status,200)
        self.assertEqual(json.loads(self.row['filter'])['task_ids'],[5]);self.row=None;self.assertEqual(self.request({'name':'x'}).status,404)
    def test_revocation_on_update_precommit_aborts_and_retains_unknown(self):
        original=self.store.executor.execute
        def execute(q):
            n=original(q);self.allowed=False;return n
        self.store.executor.execute=execute
        self.assertEqual(self.request({'name':'x'}).status,503);self.assertEqual(self.row['name'],'original')
