"""Inert authenticated router contracts, no targets/providers/evidence IO."""
import json
import unittest
from unittest.mock import Mock
from artex.server.core import Router,Request
from artex.server.findings import FindingService, BASE, MAX_BODY


def record(**updates):
    row = dict(id=7,task_id=4,node_id=9,vulnclass='CWE-79',name='',severity='high',status='',
               summary='summary',evidence='proof',report='',asset_ids=[],created_at='2026-11-15T00:00:00+00:00',
               traffic_count=1,evidence_version=2,report_evidence_version=1,task_description='Example')
    row.update(updates)
    return row


class FindingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock()
        self.store.get.return_value=record()
        self.store.list.return_value=[record()]
        self.store.list_page.return_value=([record()],1)
        self.router=Router(lambda req:req.header('Authorization')=='dummy')
        FindingService(self.store).register(self.router)

    def call(self,path='',method='GET',body=b'',auth=True):
        if isinstance(body,dict): body=json.dumps(body).encode()
        return self.router.dispatch(Request(method,BASE+path,{'Authorization':'dummy'} if auth else {},body))

    def test_auth_before_repository(self):
        for method,path in [('GET',''),('GET','/7'),('PATCH','/7'),('DELETE','/7'),('GET','/export'),('POST','/7/retests')]:
            self.assertEqual(self.call(path,method,auth=False).status,401)
        self.assertEqual(self.store.mock_calls,[])

    def test_global_bare_envelope(self):
        response=self.call()
        self.assertEqual(response.status,200)
        self.store.list.assert_called_once_with(limit=500)
        dto=response.json()[0]
        self.assertEqual(dto['id'],'7')
        self.assertEqual(dto['finding_id'],'7')
        self.assertEqual(dto['task_id'],'4')
        self.assertEqual(dto['status'],'pending')
        for key in ('node_id','worker','assets','report','name','intent_id'): self.assertNotIn(key,dto)

    def test_paged_filters(self):
        response=self.call('?page=2&limit=999&task_id=4&severity=high&q=%25')
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['page_size'],200)
        args,kw=self.store.list_page.call_args
        self.assertEqual(args[0].task_id,'4')
        self.assertEqual(args[0].query,'%')
        self.assertEqual(kw,dict(page=2,page_size=200))

    def test_scope_never_silently_broadens(self):
        for query in ('?task_id=4','?page=1&task_id=','?page=1&task_id=null','?page=1&task_id=0',
                      '?page=1&task_id=4&task_id=5','?company_id=4','?scope=4','?page=1&severity=oops',
                      '?page=1&limit=-1','?page=9999999999999999999','?page=1&unknown=4'):
            with self.subTest(query=query): self.assertEqual(self.call(query).status,400)
        self.assertEqual(self.store.mock_calls,[])

    def test_live_task_context_and_asset_tree_unavailable(self):
        for path in ('?task=4','/7?context_task=4','?page=1&asset_scope=a:4'):
            self.assertEqual(self.call(path).status,503)
        self.assertEqual(self.store.mock_calls,[])

    def test_invalid_ids_and_context(self):
        for path in ('/0','/-1','/no','/9223372036854775808','/7?context_task='):
            self.assertEqual(self.call(path).status,400)
        self.store.get.assert_not_called()

    def test_get_report_and_stale(self):
        self.store.get.return_value=record(report='Stored report')
        dto=self.call('/7').json()
        self.assertEqual(dto['report'],'Stored report')
        self.assertTrue(dto['report_stale'])

    def test_not_found(self):
        self.store.get.return_value=None
        self.assertEqual(self.call('/7').status,404)
        self.assertEqual(self.call('/7','PATCH',{'name':'new'}).status,404)
        self.store.set_name.assert_not_called()

    def test_asset_labels_resolver(self):
        self.store.get.return_value=record(asset_ids=[2,3])
        self.assertEqual(self.call('/7').status,503)
        resolver=Mock(return_value={2:dict(id=2,type='endpoint',url='https://example.invalid',method='GET')})
        self.router=Router(lambda _:True)
        FindingService(self.store,asset_resolver=resolver).register(self.router)
        self.assertEqual(self.call('/7').json()['assets'],[dict(id='2',type='endpoint',label='GET https://example.invalid')])
        resolver.assert_called_once_with([2,3])

    def test_metadata_patch(self):
        self.store.set_name.return_value=self.store.set_severity.return_value=1
        self.store.get.side_effect=[record(),record(name='new',severity='low')]
        response=self.call('/7','PATCH',{'name':' new ','severity':'low'})
        self.assertEqual(response.status,200)
        self.store.set_name.assert_called_once_with(7,'new')
        self.store.set_severity.assert_called_once_with(7,'low')
        self.assertEqual(response.json()['name'],'new')

    def test_status_notification_unavailable_before_any_writes(self):
        self.assertEqual(self.call('/7','PATCH',{'name':'new','status':'fixed'}).status,503)
        self.assertEqual(self.store.mock_calls,[])

    def test_patch_validation_before_writes(self):
        bodies=[{}, {'name':None},{'name':'x','severity':'oops'}, {'task_id':4},
                b'{"name":"a","name":"b"}',b'{"name":NaN}',b'{"name":"a"} {}',
                '{"name":"a"}'.encode('utf-16'),b'[]']
        for body in bodies:
            self.assertEqual(self.call('/7','PATCH',body).status,400)
        self.assertEqual(self.call('/7','PATCH',b' '*(MAX_BODY+1)).status,413)
        self.assertEqual(self.store.mock_calls,[])

    def test_missing_asset_adapter_prevents_patch(self):
        self.store.get.return_value=record(asset_ids=[2])
        self.assertEqual(self.call('/7','PATCH',{'name':'new'}).status,503)
        self.store.set_name.assert_not_called()

    def test_unported_routes_explicit_unavailable(self):
        for method,path in [('GET','/export'),('GET','/stats'),('GET','/groups'),('GET','/asset-tree'),
                            ('GET','/7/lineage'),('GET','/7/retests'),('POST','/7/retests'),('POST','/7/deepen'),('DELETE','/7')]:
            self.assertEqual(self.call(path,method).status,503)
        self.assertEqual(self.store.mock_calls,[])
        self.assertEqual(self.call('/7/report').status,404)

    def test_internal_errors_redacted(self):
        self.store.get.side_effect=RuntimeError('secret SQL credential')
        r=self.call('/7')
        self.assertEqual(r.status,500)
        self.assertNotIn(b'secret',r.body)


class FindingStoreHTTPTests(unittest.TestCase):
    def test_real_store_scoped_page_bound_sql(self):
        from datetime import datetime, timezone
        from artex.db.findings import FindingStore
        executor=Mock()
        executor.fetch_one.return_value=(1,)
        executor.fetch_all.return_value=[(7,4,9,'CWE-79','','high','summary','proof','worker',[],
                                         'pending',datetime(2026,11,15,tzinfo=timezone.utc),'Example',2,1,0)]
        router=Router(lambda _:True)
        FindingService(FindingStore(executor,task_id=4,exploration_id=8)).register(router)
        result=router.dispatch(Request('GET',BASE+'?page=1&task_id=4&q=%25'))
        self.assertEqual(result.status,200)
        self.assertEqual(result.json()['items'][0]['finding_id'],'7')
        query=executor.fetch_all.call_args.args[0]
        self.assertIn('f.task_id=$1',query.sql)
        self.assertIn('scope_node.exploration_id=$2',query.sql)
        self.assertEqual(query.params[:3],(4,8,4))
        self.assertEqual(query.params[-2:],(20,0))
        self.assertNotIn("ILIKE '%",query.sql)
        executor.execute.assert_not_called()

    def test_real_store_get_report_and_missing(self):
        from datetime import datetime, timezone
        from artex.db.findings import FindingStore
        executor=Mock()
        executor.fetch_all.return_value=[(7,None,None,'CWE-79','','high','summary','proof','worker',[],
                                         'pending',datetime(2026,11,15,tzinfo=timezone.utc),'',2,1,0,'Stored report')]
        router=Router(lambda _:True)
        FindingService(FindingStore(executor)).register(router)
        response=router.dispatch(Request('GET',BASE+'/7'))
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['report'],'Stored report')
        self.assertTrue(response.json()['report_stale'])
        self.assertNotIn('task_id',response.json())
        self.assertEqual(executor.fetch_all.call_args.args[0].params,(7,))
        executor.fetch_all.return_value=[]
        self.assertEqual(router.dispatch(Request('GET',BASE+'/7')).status,404)


if __name__=='__main__': unittest.main()
