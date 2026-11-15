"""Schema bootstrap contracts; no real database or upstream execution."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import re
import unittest
from artex.db.core import Query
from artex.db.schema import (SchemaExecutor,SchemaRefused,SchemaBootstrapError,
    bootstrap_empty_database,schema_sql,LOCK_KEY,SCHEMA_SHA256,IDENTITY_SQL,EMPTY_SQL,TABLES_SQL)

TABLES=sorted(re.findall(r'^CREATE TABLE IF NOT EXISTS ([a-z_]+)\s*\(',schema_sql(),re.M))

class Fake:
    def __init__(self,results=None):
        self.results=list(results if results is not None else [(None,),('fixture',180000,'off','on',0),(True,False),(TABLES,)])
        self.calls=[];self.fail_script=False;self.fail_commit=False
    @contextmanager
    def transaction(self):
        self.calls.append(('begin',None))
        try:
            yield self
            if self.fail_commit:raise RuntimeError('dummy-credential detail')
        except BaseException:self.calls.append(('rollback',None));raise
        else:self.calls.append(('commit',None))
    def fetch_one(self,q):
        self.calls.append(('query',q));return self.results.pop(0)
    def execute_script(self,sql):
        self.calls.append(('script',sql))
        if self.fail_script:raise RuntimeError('dummy-credential detail')

class SchemaTests(unittest.TestCase):
    def bootstrap(self,db,**kwargs):return bootstrap_empty_database(db,expected_database='fixture',allow_schema_creation=True,**kwargs)

    def test_resource_integrity_and_source_ddl_equivalence(self):
        sql=schema_sql()
        self.assertEqual(sha256(sql.encode()).hexdigest(),SCHEMA_SHA256)
        self.assertEqual(len(TABLES),49)
        self.assertNotRegex(sql,r'[\u3400-\u9fff]')
        self.assertIn("VALUES ('ScopeSentry', 'http', NULL",sql)
        source=Path(__file__).resolve().parents[2]/'db'/'schema.sql'
        if source.exists():
            # Decode the four simple Unicode literals; no SQL execution.
            decode=lambda value:re.sub(r"U&'([^']*)'",lambda m:"'"+re.sub(r'\\([0-9a-f]{4})',lambda n:chr(int(n[1],16)),m[1])+"'",value)
            strip=lambda text:'\n'.join(line.split('--',1)[0].rstrip() for line in text.splitlines())
            self.assertEqual(strip(decode(source.read_text())),strip(decode(sql)))

    def test_opt_in_and_maintenance_rejected_before_any_db_operation(self):
        for kwargs in ({'expected_database':'fixture'}, {'expected_database':'fixture','allow_schema_creation':1},
                       {'expected_database':'postgres','allow_schema_creation':True},
                       {'expected_database':'','allow_schema_creation':True}):
            db=Fake()
            with self.assertRaises(SchemaRefused):bootstrap_empty_database(db,**kwargs)
            self.assertEqual(db.calls,[])

    def test_success_lock_checks_script_order_and_commit(self):
        db=Fake();result=self.bootstrap(db)
        self.assertEqual(result.database,'fixture');self.assertEqual(result.table_count,49)
        self.assertFalse(result.builtins_seeded);self.assertFalse(result.existing_database_upgrades_supported)
        self.assertEqual([op for op,x in db.calls],['begin','query','query','query','script','script','query','commit'])
        self.assertEqual(db.calls[1][1],Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
        self.assertEqual(db.calls[2][1].sql,IDENTITY_SQL);self.assertEqual(db.calls[3][1].sql,EMPTY_SQL)
        self.assertEqual(db.calls[4][1],'SET LOCAL search_path = public, pg_catalog')
        self.assertEqual(db.calls[5][1],schema_sql());self.assertEqual(db.calls[6][1].sql,TABLES_SQL)

    def test_identity_version_readonly_temp_and_literal_mode_fail_closed(self):
        identities=[('other',180000,'off','on',0),('fixture',110000,'off','on',0),
                    ('fixture',180000,'on','on',0),('fixture',180000,'off','off',0),
                    ('fixture',180000,'off','on',99)]
        for identity in identities:
            with self.subTest(identity=identity):
                db=Fake([(None,),identity])
                with self.assertRaises(SchemaRefused):self.bootstrap(db)
                self.assertEqual(db.calls[-1][0],'rollback')
                self.assertFalse(any(op=='script' for op,x in db.calls))

    def test_existing_or_missing_schema_never_runs_ddl(self):
        for inventory in ((True,True),(False,False),(None,False)):
            db=Fake([(None,),('fixture',180000,'off','on',0),inventory])
            with self.assertRaises(SchemaRefused):self.bootstrap(db)
            self.assertEqual(db.calls[-1][0],'rollback')
            self.assertFalse(any(op=='script' for op,x in db.calls))
        for catalog in ('pg_class','pg_proc','pg_type','pg_extension','pg_namespace'):
            self.assertIn(catalog,EMPTY_SQL)

    def test_script_manifest_commit_failures_rollback_without_secret_echo(self):
        for failure in ('script','manifest','commit'):
            db=Fake()
            if failure=='script':db.fail_script=True
            if failure=='manifest':db.results[-1]=(['tasks'],)
            if failure=='commit':db.fail_commit=True
            with self.subTest(failure=failure),self.assertRaises(SchemaBootstrapError) as caught:self.bootstrap(db)
            self.assertNotIn('dummy-credential',str(caught.exception));self.assertEqual(db.calls[-1][0],'rollback')

    def test_modified_resource_rejected_before_transaction(self):
        db=Fake()
        fake=SimpleNamespace(joinpath=lambda name:SimpleNamespace(read_bytes=lambda:b'CREATE TABLE wrong(id int)'))
        with patch('artex.db.schema.files',return_value=fake):
            with self.assertRaises(SchemaRefused):self.bootstrap(db)
        self.assertEqual(db.calls,[])

    def test_unicode_literal_values_preserved(self):
        values=re.findall(r"U&'([^']*)'",schema_sql())
        self.assertEqual(len(values),4)
        decoded=[re.sub(r'\\([0-9a-f]{4})',lambda m:chr(int(m[1],16)),v) for v in values]
        expected=['\u4efb\u52a1\u6267\u884c\u671f\u95f4\u81ea\u52a8\u5173\u8054',
                  '\u7531\u5386\u53f2\u4efb\u52a1\u8d44\u4ea7\u5173\u8054\u8fc1\u79fb',
                  '[\u6a21\u578b]%','\u590d\u6d4b\u4f1a\u8bdd\u5df2\u5220\u9664']
        self.assertEqual(decoded,expected)


class Connection:
    def __init__(self):self.autocommit=True;self.closed=False;self.info=SimpleNamespace(transaction_status=0);self.events=[]
    @contextmanager
    def transaction(self):
        self.events.append('begin');self.info.transaction_status=2
        try:yield
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')
        finally:self.info.transaction_status=0

class Cursor:
    rowcount=-1
    def __init__(self,conn):self.conn=conn
    def __enter__(self):self.conn.events.append('cursor');return self
    def __exit__(self,*args):self.conn.events.append('close cursor')
    def execute(self,*args):self.conn.events.append(args)
    def fetchone(self):return (123,)

class SchemaAdapterTests(unittest.TestCase):
    def test_native_bind_and_multistatement_not_dml_rowcount(self):
        conn=Connection();adapter=SchemaExecutor(conn,cursor_factory=Cursor)
        with adapter.transaction():
            self.assertEqual(adapter.fetch_one(Query('SELECT $1',(123,))),(123,))
            adapter.execute_script('CREATE TABLE a(id int); DO $$ BEGIN NULL; END $$;')
        self.assertIn(('SELECT $1',(123,)),conn.events)
        self.assertIn(('CREATE TABLE a(id int); DO $$ BEGIN NULL; END $$;',),conn.events)
        self.assertEqual(conn.events[-1],'commit');self.assertFalse(conn.closed)
        self.assertEqual(conn.events.count('close cursor'),2)

    def test_nontransaction_nested_and_nonidle_refused(self):
        conn=Connection();adapter=SchemaExecutor(conn,cursor_factory=Cursor)
        with self.assertRaises(SchemaRefused):adapter.execute_script('SELECT 1')
        with adapter.transaction():
            with self.assertRaises(SchemaRefused):
                with adapter.transaction():pass
        conn.autocommit=False
        with self.assertRaises(SchemaRefused):SchemaExecutor(conn,cursor_factory=Cursor)

    def test_cursor_failure_rolls_back_without_closing_borrowed_connection(self):
        class BadCursor(Cursor):
            def execute(self,*args):raise RuntimeError('inert failure')
        conn=Connection();adapter=SchemaExecutor(conn,cursor_factory=BadCursor)
        with self.assertRaises(RuntimeError):
            with adapter.transaction():adapter.execute_script('SELECT 1')
        self.assertEqual(conn.events[-2:],['close cursor','rollback']);self.assertFalse(conn.closed)

if __name__=='__main__':unittest.main()
