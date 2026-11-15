"""Independent fixed-workspace read bounds and authority tests; temp SQLite only."""
import unittest
import test_traffic_read as fixtures

class Review(unittest.TestCase):
    setUp=fixtures.TrafficReadTests.setUp
    call=fixtures.TrafficReadTests.call
    def test_hosts_row_bound_precedes_unbounded_store_reader(self):
        self.service.max_rows=1
        self.store.record(url='https://other.test/',exchange_id='two')
        fetched=[]
        self.store.hosts=lambda:fetched.append(True) or []
        self.assertEqual(self.call('hosts').status,503)
        self.assertEqual(fetched,[])
    def test_unicode_and_nul_metadata_use_byte_length(self):
        self.service.max_bytes=1024
        self.store.db.execute('UPDATE exchanges SET url=?',('a\x00'+'é'*700,));self.store.db.commit()
        fetched=[]
        self.store.page=lambda *a:fetched.append(True) or ([],0)
        self.assertEqual(self.call().status,503)
        self.assertEqual(fetched,[])
    def test_final_authorization_denial_never_returns_secret_body(self):
        calls=[]
        def authorize(*args):calls.append(1);return len(calls)==1
        self.service.authorize=authorize
        result=self.call('exchange','?id=one')
        self.assertEqual(result.status,403);self.assertNotIn(b'answer',result.body)
    def test_selector_policy_cannot_rewrite_id(self):
        errors=[]
        def authorize(request,action,scope,query):
            try:query['id']='other'
            except TypeError:errors.append(True)
            return True
        self.service.authorize=authorize
        result=self.call('exchange','?id=one')
        self.assertEqual(result.status,200);self.assertIn('answer',result.json()['resp']);self.assertEqual(len(errors),2)
    def test_connection_swap_during_authorize_denies_before_query(self):
        original=self.store.db
        def authorize(*args):self.store.db=object();return True
        self.service.authorize=authorize
        try:self.assertEqual(self.call().status,403)
        finally:self.store.db=original
    def test_body_text_storage_still_bounded_by_utf8_bytes(self):
        # SQLite BLOB affinity does not prohibit TEXT values in a legacy/imported DB.
        self.service.max_bytes=1024
        self.store.db.execute('UPDATE exchange_bodies SET resp_body=?',('é'*700,));self.store.db.commit()
        fetched=[]
        self.store.get=lambda *a:fetched.append(True) or ('','')
        self.assertEqual(self.call('exchange','?id=one').status,503)
        self.assertEqual(fetched,[])

if __name__=='__main__':unittest.main()
