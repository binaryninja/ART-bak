import unittest,re
from contextlib import contextmanager
from pathlib import Path
from copy import deepcopy
from artex.db.recording_schema import *
ABSENT=(None,None,None,None,None,False,None)
def catalog(table,legacy=False):
    cols=[]
    for name in USAGE_COLUMNS if table=='llm_usage' else RECORD_COLUMNS:
        if legacy and name in OPTIONAL:continue
        required=name=='id' or (table=='llm_usage' and name in {'ts','input_tokens','output_tokens','cache_read','cache_write'})
        cols.append(dict(name=name,type='bigint' if name in ('id','exploration_id') else 'timestamp with time zone' if name=='ts' else 'integer' if name in INTS else 'text',required=required,default=f"nextval('{table}_id_seq'::regclass)" if name=='id' else 'now()' if name=='ts' else '0' if required else None,identity='',generated=''))
    indexes=[dict(method='btree',included=False,options='0',name=table+'_pkey',unique=True,primary=True,valid=True,partial=False,expression=False,columns=['id'])]
    indexes += [dict(method='btree',included=False,options=' '.join('0' for _ in v),name=k,unique=False,primary=False,valid=True,partial=False,expression=False,columns=v) for k,v in INDEXES[table].items()]
    return ('r',False,False,cols,indexes,False,['p'])
class Fake:
    def __init__(self,pre=None):self.rows=[(None,),('fixture',170011,'off','on',0),*(pre or [ABSENT,ABSENT]),catalog('llm_usage'),catalog('llm_records')];self.calls=[];self.events=[];self.fail=None
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')
    def fetch_one(self,q):self.calls.append(q);return self.rows.pop(0)
    def execute_script(self,s):
        self.calls.append(s)
        if self.fail==s:raise RuntimeError('fixture secret')
class RecordingSchemaTests(unittest.TestCase):
    def run_setup(self,db):return ensure_recording_schema(db,expected_database='fixture',action='create_or_upgrade')
    def test_create_atomic_lock_and_no_recording_enablement(self):
        db=Fake();result=self.run_setup(db)
        self.assertFalse(result.recording_enabled);self.assertEqual(db.events,['begin','commit']);self.assertEqual(db.calls[0].params,(LOCK_KEY,))
        self.assertEqual([s for s in db.calls if isinstance(s,str)],["SET LOCAL search_path = public, pg_catalog",USAGE_SQL,RECORDS_SQL,MIGRATION_SQL])
    def test_current_and_legacy_supported(self):
        for legacy in (False,True):
            db=Fake([catalog('llm_usage'),catalog('llm_records',legacy)]);self.run_setup(db);self.assertEqual(db.events[-1],'commit')
    def test_wrong_target_refuses_before_ddl(self):
        db=Fake();db.rows[1]=('other',170011,'off','on',0)
        with self.assertRaises(SchemaRefused):self.run_setup(db)
        self.assertFalse(any(isinstance(s,str) for s in db.calls));self.assertEqual(db.events[-1],'rollback')
    def test_optin_and_maintenance_no_transaction(self):
        db=Fake()
        with self.assertRaises(SchemaRefused):ensure_recording_schema(db,expected_database='fixture')
        with self.assertRaises(SchemaRefused):ensure_recording_schema(db,expected_database='postgres',action='create_or_upgrade')
        self.assertEqual(db.events,[])
    def test_invalid_existing_columns_indexes_and_triggers_refused(self):
        for mutation in ('type','index','trigger'):
            row=list(deepcopy(catalog('llm_usage')))
            if mutation=='type':row[3][0]['type']='integer'
            elif mutation=='index':row[4][1]['columns']=['worker']
            else:row[5]=True
            db=Fake([tuple(row),ABSENT])
            with self.assertRaises(SchemaRefused):self.run_setup(db)
            self.assertNotIn(USAGE_SQL,db.calls);self.assertEqual(db.events[-1],'rollback')
    def test_ddl_failure_and_postcondition_roll_back(self):
        db=Fake();db.fail=MIGRATION_SQL
        with self.assertRaises(SchemaBootstrapError) as caught:self.run_setup(db)
        self.assertNotIn('secret',str(caught.exception));self.assertEqual(db.events[-1],'rollback')
        db=Fake();db.rows[-1]=ABSENT
        with self.assertRaises(SchemaRefused):self.run_setup(db)
        self.assertEqual(db.events[-1],'rollback')
    def test_source_sql_exact(self):
        root=Path(__file__).resolve().parents[2]
        for path,name,sql in [('db/llm_usage.go','llmUsageSchema',USAGE_SQL),('db/commands.go','llmRecordsSchema',RECORDS_SQL),('db/commands.go','llmRecordsMigrate',MIGRATION_SQL)]:
            source=(root/path).read_text();self.assertEqual(re.search(r'const '+name+r' = `([\s\S]*?)`',source).group(1),sql)
