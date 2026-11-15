"""Task metadata and batch-control contracts with inert managers/repositories."""
import json
import unittest
from copy import deepcopy
from artex.server.core import Router,Request
from artex.server.tasks import TaskService
from artex.manager import TaskManager,RuntimeUnavailable,TaskConflict
from test_server_tasks import public_task,FakeManager


class Manager(FakeManager):
    def update_metadata(self,task_id,**patch):
        self.calls.append(('metadata',task_id,patch))
        self.tasks[task_id].update(patch)
        return deepcopy(self.tasks[task_id])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.manager=Manager()
        self.router=Router(lambda req:req.header('Authorization')=='dummy')
        TaskService(self.manager).register(self.router)

    def call(self,path='/api/tasks/1',method='PATCH',data=None,auth=True):
        body=json.dumps(data).encode() if isinstance(data,dict) else data or b''
        return self.router.dispatch(Request(method,path,{'Authorization':'dummy'} if auth else {},body))

    def batch(self,data):return self.call('/api/tasks/control/batch','POST',data)

    def test_auth_precedes_metadata_and_batch(self):
        self.assertEqual(self.call(data={'name':'new'},auth=False).status,401)
        self.assertEqual(self.call('/api/tasks/control/batch','POST',{'action':'pause','task_ids':['1']},auth=False).status,401)
        self.assertEqual(self.manager.calls,[])

    def test_metadata_trim_pin_false_not_lifecycle(self):
        before=deepcopy(self.manager.tasks['1'])
        r=self.call(data={'name':' New\u3000','pinned':False})
        self.assertEqual(r.status,200)
        self.assertEqual(self.manager.calls,[('metadata','1',{'name':'New','pinned':False})])
        for field in ('description','goal','status','paused','queued'):
            self.assertEqual(r.json()[field],before[field])

    def test_metadata_blank_reset_rejected_null_omission(self):
        for data in ({},{'name':''},{'name':' \t'},{'name':None},{'pinned':None},{'pinned':1},{'name':'x'*201},{'goal':'new'}):
            self.assertEqual(self.call(data=data).status,400)
        self.assertEqual(self.manager.calls,[])
        self.assertEqual(self.call(data={'name':None,'pinned':False}).status,200)
        self.assertEqual(self.manager.calls[-1],('metadata','1',{'pinned':False}))

    def test_utf8_duplicate_keys_size_and_missing_task(self):
        for data in (b'{"name":"one","name":"two"}',b'{"pinned":NaN}',b'[]','{}'.encode('utf-16')):
            self.assertEqual(self.call(data=data).status,400)
        self.assertEqual(self.call(data=b' '*(16384+1)).status,413)
        self.assertEqual(self.call('/api/tasks/2',data={'name':'new'}).status,404)
        self.assertEqual(self.call('/api/tasks/0',data={'name':'new'}).status,400)
        self.assertEqual(self.manager.calls,[])

    def test_batch_canonical_order_dedupe_and_per_item_errors(self):
        r=self.batch({'action':'pause','task_ids':[' +001 ','1',' 2 ','oops','oops','0']})
        self.assertEqual(r.status,200)
        self.assertEqual(r.json(),{'items':[{'id':'1','ok':True,'status':'paused'},
            {'id':'2','ok':False,'error':'task not found'},{'id':'oops','ok':False,'error':'bad task id'},
            {'id':'0','ok':False,'error':'bad task id'}]})
        self.assertEqual(self.manager.calls,[('control','1','pause')])

    def test_batch_limits_after_normalization(self):
        self.assertEqual(self.batch({'action':'pause','task_ids':['1']*101}).status,200)
        self.manager.calls.clear()
        self.assertEqual(self.batch({'action':'pause','task_ids':[str(i) for i in range(1,102)]}).status,400)
        self.assertEqual(self.batch({'action':'pause','task_ids':[]}).status,400)
        self.assertEqual(self.batch(b' '*(262144+1)).status,400)
        self.assertEqual(self.manager.calls,[])

    def test_batch_validation_before_any_action(self):
        for data in ({'action':'stop','task_ids':['1']},{'action':'pause','task_ids':[1]},
                     {'action':'pause','task_ids':['1'],'force':True},b'{"action":"pause","action":"resume","task_ids":["1"]}'):
            self.assertEqual(self.batch(data).status,400)
        self.assertEqual(self.manager.calls,[])

    def test_batch_runtime_unavailable_conflict_and_internal_error(self):
        for error,label in ((RuntimeUnavailable('secret'),'unavailable'),(TaskConflict('secret'),'conflicts'),(RuntimeError('secret'),'failed')):
            self.manager.error=error
            r=self.batch({'action':'resume','task_ids':['1']})
            self.assertEqual(r.status,200);self.assertFalse(r.json()['items'][0]['ok'])
            self.assertIn(label,r.json()['items'][0]['error']);self.assertNotIn(b'secret',r.body)
        self.assertEqual(self.manager.calls,[])

    def test_partial_success_preserved_and_queued_true_only(self):
        def control(ident,action):
            if ident=='2':raise TaskConflict()
            return dict(id=ident,status='queued',queued=True,paused=False)
        self.manager.tasks['2']=public_task('2')
        self.manager.control_task=control
        r=self.batch({'action':'resume','task_ids':['1','2']})
        self.assertEqual(r.json()['items'][0],dict(id='1',ok=True,status='queued',queued=True))
        self.assertFalse(r.json()['items'][1]['ok'])

    def test_invalid_control_return_not_success_or_leak(self):
        self.manager.control_task=lambda *a:{'id':'other','status':{'secret':'private'},'queued':False,'paused':True}
        r=self.batch({'action':'pause','task_ids':['1']})
        self.assertFalse(r.json()['items'][0]['ok']);self.assertNotIn(b'private',r.body)

    def test_real_manager_metadata_without_start_and_missing_metrics_preflight(self):
        class Repository:
            def __init__(self):
                self.row={**public_task('1'),'id':1,'pinned_at':None}
                self.writes=[]
            def list(self):return [deepcopy(self.row)]
            def update(self,identifier,**patch):
                self.writes.append((identifier,patch))
                if 'name' in patch:self.row['name']=patch['name']
                if 'pinned' in patch:self.row['pinned_at']='2026-11-15T00:00:00Z' if patch['pinned'] else None
                return deepcopy(self.row)
        repository=Repository()
        manager=TaskManager(repository,dto_enricher=lambda dto:public_task())
        manager.load_existing()
        self.router=Router(lambda _:True);TaskService(manager).register(self.router)
        r=self.call(data={'name':' New ','pinned':True})
        self.assertEqual(r.status,200);self.assertTrue(r.json()['pinned'])
        self.assertEqual(r.json()['name'],'New');self.assertEqual(r.json()['status'],'created')
        self.assertEqual(repository.writes,[(1,{'name':'New','pinned':True})])
        manager.dto_enricher=None
        self.assertEqual(self.call(data={'pinned':False}).status,503)
        self.assertEqual(len(repository.writes),1)

    def test_real_manager_missing_runtime_never_resumes(self):
        class Repository:
            def list(self):return [{**public_task('1','paused'),'id':1,'status':'running','paused':True,'pinned_at':None}]
            def update(self,*a,**kw):raise AssertionError('No metadata writes expected')
        manager=TaskManager(Repository(),dto_enricher=lambda dto:public_task('1','paused'))
        manager.load_existing()
        self.router=Router(lambda _:True);TaskService(manager).register(self.router)
        response=self.batch({'action':'resume','task_ids':['1']})
        self.assertEqual(response.status,200)
        self.assertFalse(response.json()['items'][0]['ok'])
        self.assertIn('unavailable',response.json()['items'][0]['error'])


if __name__=='__main__':unittest.main()
