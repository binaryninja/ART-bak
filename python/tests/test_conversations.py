import json,unittest
from contextlib import contextmanager
from datetime import datetime,timezone
from artex.db.conversations import ConversationStore,Conversation,ConversationInvalid,ConversationMissing,ConversationUnavailable
from artex.server.conversations import ConversationService
from artex.server.core import Router,Request
NOW=datetime(2026,11,15,tzinfo=timezone.utc)
ROW=(1,'assistant','Title',None,None,NOW,NOW)
class Executor:
    def __init__(self):self.calls=[];self.events=[];self.queue=[];self.rows=[ROW]
    def fetch_one(self,q):self.calls.append(q);return self.queue.pop(0) if self.queue else ROW
    def fetch_all(self,q):self.calls.append(q);return self.rows
    def execute(self,q):self.calls.append(q);return 1
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.store=ConversationStore(self.db)
        self.service=ConversationService(self.store,agent_available=lambda key:True,profile_available=lambda id:True,running=lambda id:False)
        self.router=Router(lambda request:request.header('Authorization')=='yes');self.service.register(self.router)
    def request(self,method='GET',path='/api/conversations',data=None,body=None,auth=True):
        return self.router.dispatch(Request(method,path,{'Authorization':'yes'} if auth else {},body if body is not None else json.dumps(data).encode() if data is not None else b''))
    def test_auth_and_list_shape(self):
        self.assertEqual(self.request(auth=False).status,401);self.assertEqual(self.db.calls,[])
        item=self.request().json()['conversations'][0]
        self.assertFalse(item['running']);self.assertFalse(item['pinned']);self.assertNotIn('llm_profile_id',item);self.assertNotIn('session_id',item)
        self.assertIn('pinned_at DESC NULLS LAST, updated_at DESC, id DESC',self.db.calls[-1].sql)
        self.assertEqual(self.store.get(1).session_id,'conv-1')
    def test_get_only_store_not_invented_route(self):
        self.assertEqual(self.request(path='/api/conversations/1').status,405)
        self.db.queue=[None];self.assertIsNone(self.store.get(123));self.assertEqual(self.db.calls[-1].params,(123,))
    def test_create_binding_and_defaults(self):
        response=self.request('POST',data={'agent_key':' assistant ','title':" x');-- "})
        self.assertEqual(response.status,200);self.assertEqual(self.db.calls[-1].params,('assistant',"x');--"));self.assertNotIn("x');--",self.db.calls[-1].sql)
        self.store.create('assistant');self.assertEqual(self.db.calls[-1].params,('assistant','New conversation'))
    def test_profile_child_first_lock_order(self):
        self.db.queue=[ROW,(8,),(*ROW[:3],8,*ROW[4:])]
        result=self.store.create('assistant','title',8)
        self.assertEqual(result.llm_profile_id,8)
        self.assertTrue(self.db.calls[0].sql.startswith('INSERT'));self.assertIn('FOR KEY SHARE',self.db.calls[1].sql);self.assertTrue(self.db.calls[2].sql.startswith('UPDATE'))
        self.assertEqual(self.db.events,['begin','commit'])
    def test_missing_profile_rolls_back_child(self):
        self.db.queue=[ROW,None]
        with self.assertRaises(ConversationInvalid):self.store.create('assistant','t',8)
        self.assertEqual(self.db.events,['begin','rollback'])
    def test_profile_update_lock_clear_missing(self):
        self.db.queue=[(1,)]
        self.store.update_profile(1,None)
        self.assertIn('FOR UPDATE',self.db.calls[0].sql);self.assertEqual(self.db.calls[1].params,(1,None))
        self.db.queue=[None]
        with self.assertRaises(ConversationMissing):self.store.update_profile(1,2)
    def test_patch_atomic_omission_and_unpin(self):
        result=self.request('PATCH','/api/conversations/1',{'title':' Renamed '})
        self.assertEqual(result.status,200);self.assertEqual(self.db.calls[-1].params,(1,True,'Renamed',None))
        self.request('PATCH','/api/conversations/1',{'pinned':False})
        self.assertEqual(self.db.calls[-1].params,(1,False,None,False));self.assertIn('COALESCE(pinned_at, now())',self.db.calls[-1].sql)
    def test_missing_caps_no_false_idle_or_create(self):
        self.service.running=None;self.assertEqual(self.request().status,503)
        self.service.agent_available=None;self.assertEqual(self.request('POST',data={'agent_key':'assistant'}).status,503)
        self.service.agent_available=lambda key:True;self.service.profile_available=None
        self.assertEqual(self.request('POST',data={'agent_key':'assistant','llm_profile_id':2}).status,503)
    def test_create_validation_before_write(self):
        for data in ({},{'agent_key':None},{'agent_key':'a'*121},{'agent_key':'a','title':'a'*201},{'agent_key':'a','llm_profile_id':True},{'agent_key':'a','extra':1}):
            self.assertEqual(self.request('POST',data=data).status,400)
        self.assertEqual(self.db.calls,[])
        self.service.agent_available=lambda key:False;self.assertEqual(self.request('POST',data={'agent_key':'missing'}).status,404)
    def test_ids_json_and_size(self):
        for id in ('0','-1','99999999999999999999','x'):
            self.assertEqual(self.request('PATCH','/api/conversations/'+id,{'title':'t'}).status,400)
        for raw in (b'[]',b'{"agent_key":"a","agent_key":"b"}',b'{}{}',b'{"agent_key":NaN}'):
            self.assertEqual(self.request('POST',body=raw).status,400)
        self.assertEqual(self.request('POST',body=b' '*(65537)).status,413)
        self.assertEqual(self.request(path='/api/conversations?agent=x').status,400)
    def test_delete_lifecycle_unavailable(self):
        self.assertEqual(self.request('DELETE','/api/conversations/1').status,503)
        self.assertEqual(self.request('POST','/api/conversations/delete/batch',{'ids':[1]}).status,503)
        with self.assertRaises(ConversationUnavailable):self.store.delete(1)
        self.assertEqual(self.db.calls,[])
    def test_profile_http_and_redaction(self):
        response=self.request('PATCH','/api/conversations/1/profile',{'llm_profile_id':None})
        self.assertEqual(response.json(),{'ok':True})
        self.assertEqual(self.request('PATCH','/api/conversations/1/profile',{}).status,400)
        def broken():raise RuntimeError('secret password')
        self.store.list=broken;response=self.request();self.assertEqual(response.status,500);self.assertNotIn('secret',response.body.decode())

class DriverMissingTests(unittest.TestCase):
    def test_real_driver_no_rows_semantics(self):
        from artex.db.postgres import NoRowsError
        class Driver(Executor):
            def fetch_one(self,q):
                value=super().fetch_one(q)
                if isinstance(value,BaseException):raise value
                return value
        for operation,queue,expected in (
            (lambda s:s.get(99),[NoRowsError()],None),
            (lambda s:s.patch(99,{'title':'x'}),[NoRowsError()],None),
            (lambda s:s.update_profile(99,1),[NoRowsError()],ConversationMissing),
            (lambda s:s.create('assistant','x',3),[ROW,NoRowsError()],ConversationInvalid),
            (lambda s:s.update_profile(1,3),[(1,),NoRowsError()],ConversationInvalid),
            (lambda s:s.create('assistant'),[NoRowsError()],ConversationUnavailable),
            (lambda s:s.create('assistant','x',3),[ROW,(3,),NoRowsError()],ConversationUnavailable),
        ):
            db=Driver();db.queue=queue;store=ConversationStore(db)
            if expected is None:self.assertIsNone(operation(store))
            else:
                with self.assertRaises(expected):operation(store)
                self.assertEqual(db.events,['begin','rollback'])
        error=RuntimeError('fixture driver error');db=Driver();db.queue=[error]
        with self.assertRaises(RuntimeError) as caught:ConversationStore(db).get(1)
        self.assertIs(caught.exception,error)
