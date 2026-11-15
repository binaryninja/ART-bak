import json
import unittest
from unittest.mock import Mock
from artex.server.core import Router,Request
from artex.server.tool_catalog import ToolCatalogService
from artex.db.tool_catalog import CatalogTool,ToolCatalogStore
from test_tool_catalog import ROW,SQL

class ToolHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock();self.store.list.return_value=[CatalogTool(*ROW)];self.store.usage_counts.return_value={'fixture':3}
        self.router=Router(lambda r:r.header('Authorization')=='fixture');self.service=ToolCatalogService(self.store);self.service.register(self.router)
    def call(self,path='',method='GET',data=None,auth=True):
        body=json.dumps(data).encode() if isinstance(data,dict) else data or b''
        return self.router.dispatch(Request(method,'/api/tools'+path,{'Authorization':'fixture'} if auth else {},body))
    def test_auth_and_dto(self):
        self.assertEqual(self.call(auth=False).status,401);self.assertEqual(self.store.mock_calls,[])
        self.assertEqual(self.call().json(),{'tools':[CatalogTool(*ROW).to_dict(calls=3)]})
    def test_actual_create_no_execution_and_conflict(self):
        self.service.store=ToolCatalogStore(SQL([[(1,)],('fixture',)]))
        body=dict(key='fixture',description='data only',schema={},agents=['custom'],enabled=True,kind='command',exec={'command':'not executed'},deferred=False)
        self.assertEqual(self.call('/custom','POST',body).json(),{'key':'fixture'})
        from artex.db.postgres import NoRowsError
        self.service.store=ToolCatalogStore(SQL([[(1,)],NoRowsError()]))
        self.assertEqual(self.call('/custom','POST',body).status,409)
    def test_unsupported_test_reset_and_unknown_flags(self):
        self.assertEqual(self.call('/custom/test','POST',{'exec':{'command':'never'}}).status,503)
        self.assertEqual(self.call('/fixture/reset','POST').status,503)
        self.assertEqual(self.call('/custom','POST',{'key':'x','approval':True}).status,400)
        self.store.create.assert_not_called()
    def test_immutable_key_and_duplicates(self):
        self.assertEqual(self.call('/custom/fixture','PUT',{'key':'other'}).status,400)
        self.assertEqual(self.call('/custom','POST',b'{"key":"x","key":"y"}').status,400)
        self.assertEqual(self.call('?x=1').status,400)
    def test_missing_and_delete(self):
        self.store.get.return_value=None;self.assertEqual(self.call('/missing','PUT',{}).status,404)
        self.assertEqual(self.call('/custom/fixture','DELETE').json(),{'deleted':'fixture'})
        self.store.delete.assert_called_once_with('fixture')
