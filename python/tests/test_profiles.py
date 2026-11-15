from contextlib import contextmanager
import json
import unittest
from artex.db.profiles import LLMProfile,ProfileStore,RetryRule,RetryOverride,ProfileInvalid,ProfileNotFound,ProfileStorageError

KEY='dummy-fixture-key-1234'
ROW=(7,'Test','openai','https://example.test/v1','','model',KEY,0.0,0.0,200,'high',True,3,False,'enabled',True,100,'max_completion_tokens','X-Session',0,0,-1,0,2,100)

class Fake:
    def __init__(self,results=()):self.results=list(results);self.calls=[]
    def call(self,op,q):
        self.calls.append((op,q));result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return result
    def fetch_all(self,q):return self.call('all',q)
    def fetch_one(self,q):return self.call('one',q)
    def execute(self,q):return self.call('execute',q)
    @contextmanager
    def transaction(self):
        self.calls.append(('begin',None))
        try:yield self
        except BaseException:
            self.calls.append(('rollback',None));raise
        else:self.calls.append(('commit',None))

class ProfileTests(unittest.TestCase):
    def profile(self,**kwargs):return LLMProfile(name='Test',model='model',**kwargs)

    def test_private_public_reads_exact_fields(self):
        public=list(ROW);public[6]='…1234'
        db=Fake([[tuple(public)],[ROW],[],[ROW]])
        store=ProfileStore(db)
        p=store.list()[0]
        self.assertEqual(p.api_key,'');self.assertEqual(p.api_key_hint,'…1234')
        self.assertNotIn("COALESCE(api_key,'')",db.calls[0][1].sql)
        p=store.get(7)
        self.assertEqual(p.api_key,KEY);self.assertEqual(p.retry.empty.attempts,-1)
        self.assertEqual(p.session_header_key,'X-Session');self.assertTrue(p.streaming)
        self.assertEqual(db.calls[1][1].params,(7,))
        self.assertIsNone(store.active())
        self.assertEqual(store.pool()[0].api_key,KEY)
        self.assertIn('is_default DESC, priority DESC, id ASC',db.calls[-1][1].sql)

    def test_public_repr_never_key_or_url_credentials(self):
        p=self.profile(api_key=KEY,base_url='https://user:dummy-password@example.test/v1?token=dummy-token',proxy='socks5://user:dummy-pass@example.test:9',api_key_hint=KEY)
        public=json.dumps(p.public_dict())
        for secret in (KEY,'dummy-password','dummy-token','dummy-pass'):
            self.assertNotIn(secret,public);self.assertNotIn(secret,repr(p))
        self.assertNotIn('api_key"',public)
        self.assertEqual(p.api_key,KEY)

    def test_insert_binds_secret_and_clamps_retry_no_mutation(self):
        db=Fake([(8,)])
        p=self.profile(api_key=KEY,retry=RetryOverride(connect=RetryRule(99,-3),stream=RetryRule(-9,99999999)))
        self.assertEqual(ProfileStore(db).save(p),8)
        q=db.calls[1][1]
        self.assertIn('INSERT INTO llm_profiles',q.sql);self.assertNotIn(KEY,q.sql)
        self.assertEqual(q.params[5:7],(KEY,'…1234'))
        self.assertEqual(q.params[-6:],(20,0,0,0,-1,3600000))
        self.assertEqual(p.retry.connect.attempts,99)
        self.assertEqual(db.calls[-1][0],'commit')
        self.assertNotIn('is_default',q.sql)

    def test_update_empty_key_preserves_existing(self):
        db=Fake([1])
        self.assertEqual(ProfileStore(db).save(self.profile(id=7,api_key='')),7)
        q=db.calls[1][1]
        self.assertNotIn('api_key=',q.sql);self.assertNotIn('api_key_hint=',q.sql)
        self.assertEqual(q.params[-1],7)
        self.assertIn('session_header_key=',q.sql)
        self.assertNotIn("session_header_key=NULLIF",q.sql)

    def test_update_key_and_missing_row(self):
        db=Fake([1]);ProfileStore(db).save(self.profile(id=7,api_key=KEY))
        self.assertIn('api_key=$6',db.calls[1][1].sql)
        self.assertEqual(db.calls[1][1].params[5],KEY)
        db=Fake([0])
        with self.assertRaises(ProfileNotFound):ProfileStore(db).save(self.profile(id=99))
        self.assertEqual(db.calls[-1][0],'rollback')

    def test_non_openai_field_normalized_and_short_key_no_hint(self):
        db=Fake([(8,)])
        ProfileStore(db).save(self.profile(format='anthropic',max_tokens_field='max_completion_tokens',api_key='abc',api_key_hint='unsafe'))
        q=db.calls[1][1]
        self.assertEqual(q.params[6],'')
        self.assertEqual(q.params[16],'')

    def test_display_placeholders_cannot_overwrite_credentials(self):
        original=self.profile(id=7,base_url='https://user:secret@example.test/v1?key=secret#secret',
                              proxy='socks5://user:secret@example.test:9')
        public=original.public_dict()
        cases=[dict(base_url=public['base_url']),dict(proxy=public['proxy']),
               dict(base_url='https://example.test/?%5Bredacted%5D'),
               dict(base_url='https://example.test/#%5Bredacted%5D'),
               dict(proxy='[redacted URL]')]
        cases.extend(dict(api_key=key) for key in ('…1234','...1234','[redacted]',' [REDACTED KEY] '))
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                db=Fake()
                with self.assertRaises(ProfileInvalid):
                    ProfileStore(db).save(self.profile(id=7,**kwargs))
                self.assertEqual(db.calls,[])
        self.assertIn('#[redacted]',public['base_url'])

    def test_short_keys_never_generate_full_key_hint(self):
        for key in ('','a','abc','1234','12345'):
            db=Fake([(8,)])
            ProfileStore(db).save(self.profile(api_key=key))
            self.assertEqual(db.calls[1][1].params[6], '…2345' if len(key)>4 else '')

    def test_validation_no_queries_no_secret_in_error(self):
        cases=[dict(format='bad'),dict(rate_per_second=float('nan')),dict(context_window_k=1001),
               dict(streaming=1),dict(session_header_key='X-Test\r\nAuthorization'),
               dict(proxy='invalid:'+KEY),dict(max_tokens=-1),dict(thinking_type='maybe'),
               dict(api_key='bad\x00'+KEY),dict(retry=RetryOverride(connect=RetryRule(True,0)))]
        for kwargs in cases:
            db=Fake()
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ProfileInvalid) as raised:ProfileStore(db).save(self.profile(**kwargs))
                self.assertNotIn(KEY,str(raised.exception));self.assertEqual(db.calls,[])

    def test_driver_failures_are_redacted_and_roll_back(self):
        for read in (False,True):
            db=Fake([RuntimeError('database detail includes '+KEY)])
            with self.assertRaises(ProfileStorageError) as raised:
                ProfileStore(db).get(7) if read else ProfileStore(db).save(self.profile(api_key=KEY))
            self.assertNotIn(KEY,str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)
            if not read:self.assertEqual(db.calls[-1][0],'rollback')

    def test_set_active_transaction_and_missing_rollback(self):
        db=Fake([1,1]);ProfileStore(db).set_active(7)
        self.assertEqual([x[0] for x in db.calls],['begin','execute','execute','commit'])
        self.assertEqual(db.calls[2][1].params,(7,))
        db=Fake([1,0])
        with self.assertRaises(ProfileNotFound):ProfileStore(db).set_active(99)
        self.assertEqual(db.calls[-1][0],'rollback')

    def test_delete_explicitly_unavailable(self):
        db=Fake()
        with self.assertRaises(NotImplementedError):ProfileStore(db).delete(7)
        self.assertEqual(db.calls,[])

if __name__=='__main__':unittest.main()
