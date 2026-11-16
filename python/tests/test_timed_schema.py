"""Catalog-only fixtures. No server, schema script or sequence is executed."""
from copy import deepcopy
import unittest
from artex.db import timed_schema as m

def descriptor():
    body=dict(database='fixture',schema='public',version=170011,temp_shadow_count=0,search_path='public',replication_role='origin',constraint_triggers=[],relations=[],columns=[],constraints=[],indexes=[],sequences=[])
    for name in m._NAMES:
        body['relations'].append(dict(name=name,resolves_exact=True,kind='S' if name in m._SEQUENCES else 'r',persistence='p',partition=False,rls=False,force_rls=False,inheritance=0,triggers=0,rules=0,policies=0,dropped_columns=0))
    for name in (*m._TABLES,'tasks','explorations'):
        spec=m._MANIFEST.get(name,dict(columns=(('id','int8',True,None),),pk=('id',),unique=(),checks=()))
        for pos,(column,typ,nonnull,default) in enumerate(spec['columns'],1):
            seq=name+'_'+column+'_seq'
            expr={'serial':f"nextval('{seq}'::regclass)",'1':'1','array':"'{}'::bigint[]",'now':'now()',None:None}[default]
            body['columns'].append(dict(table_name=name,name=column,position=pos,type_name=typ,type_schema='pg_catalog',typmod=-1,not_null=nonnull,identity='',generated='',dimensions=1 if typ=='_int8' else 0,default_expr=expr,custom_dependencies=0,sequence_refs=[['public',seq]] if default=='serial' else None))
        def constraint(kind,columns=None,expression=None,foreign_table=None,foreign_columns=None):
            return dict(custom_dependencies=0,table_name=name,kind=kind,validated=True,deferrable=False,deferred=False,no_inherit=kind in ('p','u','f'),columns=columns,expression=expression,foreign_schema='public' if foreign_table else None,foreign_table=foreign_table,foreign_columns=foreign_columns,update_action='a' if foreign_table else ' ',delete_action='a' if foreign_table else ' ',match_type='s' if foreign_table else ' ')
        for kind,key in [('p',spec['pk']),*(('u',key) for key in spec['unique'])]:
            body['constraints'].append(constraint(kind,list(key)))
            n=len(key);body['indexes'].append(dict(table_name=name,primary_key=kind=='p',unique_key=True,valid=True,ready=True,immediate=True,key_count=n,total_count=n,method='btree',no_expressions=True,no_predicate=True,columns=list(key),opclasses=[['pg_catalog','int8_ops']]*n,options=[0]*n,collations=[0]*n))
        for expr in spec['checks']:body['constraints'].append(constraint('c',expression=expr))
        if name in m._TABLES:
            for col,table in [('task_id','tasks'),('exploration_id','explorations')]+([('attempt_id','artex_final_assessment')] if name=='artex_final_settlement' else []):
                body['constraints'].append(constraint('f',[col],foreign_table=table,foreign_columns=['attempt_id' if col=='attempt_id' else 'id']))
    for name,table,column in [(m._SEQUENCES[0],'artex_task_bootstrap','run_id'),(m._SEQUENCES[1],'artex_final_assessment','attempt_id')]:
        body['sequences'].append(dict(name=name,type_name='int8',start=1,increment=1,maximum=2**63-1,minimum=1,cache=1,cycle=False,owners=[['public',table,column,'a']]))
    for c in body['constraints']:
        if c['kind']!='f':continue
        source=c['table_name'];target=c['foreign_table']
        base=dict(table_schema='public',enabled='O',deferrable=False,deferred=False,function_schema='pg_catalog',args='',condition=None,constraint_kind='f',constraint_schema='public',source_schema='public',source_table=source,target_schema='public',target_table=target,source_columns=c['columns'],target_columns=c['foreign_columns'],update_action='a',delete_action='a',match_type='s')
        for table,typ,fn in ((source,5,'RI_FKey_check_ins'),(source,17,'RI_FKey_check_upd'),(target,9,'RI_FKey_noaction_del'),(target,17,'RI_FKey_noaction_upd')):
            body['constraint_triggers'].append(dict(base,table_name=table,type=typ,function_name=fn))
    for key in ('relations','columns','constraints','indexes','sequences','constraint_triggers'):body[key+'_count']=len(body[key])
    return body

class DB:
    def __init__(self,body):self.body=body;self.calls=[]
    def fetch_all(self,q):self.calls.append(q);return [(self.body,)]

class Tests(unittest.TestCase):
    def test_positive_one_native_bound_catalog_snapshot(self):
        db=DB(descriptor());result=m.validate_timed_schema(db,expected_database='fixture')
        self.assertEqual(result.server_major,17);self.assertFalse(result.source_readiness);self.assertEqual(result.tables,m._TABLES)
        self.assertEqual(len(db.calls),1);self.assertEqual(db.calls[0].params[0],'public')
        self.assertNotIn('nextval(',db.calls[0].sql);self.assertNotIn('SET ',db.calls[0].sql)
        self.assertEqual(len(result.descriptor_digest),64)
    def reject(self,edit):
        body=descriptor();edit(body)
        with self.assertRaises(m.TimedSchemaRefused):m.validate_timed_schema(DB(body),expected_database='fixture')
    def test_identity_unknown_major_missing_objects_and_overflow(self):
        for edit in [lambda b:b.update(database='other'),lambda b:b.update(version=180001),lambda b:b['relations'].pop(),lambda b:b.update(columns_count=129)]:
            with self.subTest(edit=edit):self.reject(edit)
    def test_columns_type_nullable_width_default_order(self):
        for key,value in [('type_schema','custom'),('type_name','int4'),('not_null',False),('position',9),('identity','a'),('generated','s'),('typmod',8)]:
            self.reject(lambda b,k=key,v=value:b['columns'][0].update({k:v}))
        self.reject(lambda b:b['columns'][6].update(default_expr='custom_revision()'))
    def test_constraints_flags_expression_and_target(self):
        for key,value in [('validated',False),('deferrable',True),('deferred',True),('no_inherit',False)]:
            self.reject(lambda b,k=key,v=value:b['constraints'][0].update({k:v}))
        self.reject(lambda b:next(c for c in b['constraints'] if c['kind']=='c').update(expression='true'))
        self.reject(lambda b:next(c for c in b['constraints'] if c['kind']=='f').update(foreign_table='other'))
    def test_native_constraint_noinherit_is_kind_specific(self):
        # Actual PG17 p/u/f catalog flags are true; ordinary CHECK is false.
        for kind in ('p','u','f'):
            self.reject(lambda b,k=kind:next(c for c in b['constraints'] if c['kind']==k).update(no_inherit=False))
        self.reject(lambda b:next(c for c in b['constraints'] if c['kind']=='c').update(no_inherit=True))

    def test_indexes_not_partial_custom_invalid_or_include(self):
        for key,value in [('valid',False),('ready',False),('no_predicate',False),('total_count',2),('opclasses',[['custom','int8_ops']]),('method','hash')]:
            self.reject(lambda b,k=key,v=value:b['indexes'][0].update({k:v}))
    def test_catalog_oid_collations_are_numeric_before_json_transfer(self):
        # PG17 jsonb_agg(oid) yields strings, unlike smallint indoption.
        # Cast at SQL projection, never coerce arbitrary decoder strings.
        self.assertIn('pg_catalog.jsonb_agg(x.v::bigint ORDER BY x.n) FROM pg_catalog.unnest(i.indcollation::oid[])',m.SNAPSHOT_SQL)
        self.reject(lambda b:b['indexes'][0].update(collations=['0']))
        self.reject(lambda b:b['indexes'][0].update(collations=[False]))

    def test_sequence_contract_and_default_dependency(self):
        for key,value in [('cycle',True),('increment',2),('cache',2),('owners',[['public','other','id','a']])]:
            self.reject(lambda b,k=key,v=value:b['sequences'][0].update({k:v}))
        self.reject(lambda b:b['columns'][2].update(custom_dependencies=0,sequence_refs=[['public','other_seq']]))
    def test_extension_behavior_not_table_exists_only(self):
        for key,value in [('kind','v'),('persistence','u'),('rls',True),('force_rls',True),('triggers',1),('rules',1),('policies',1),('inheritance',1)]:
            self.reject(lambda b,k=key,v=value:b['relations'][0].update({k:v}))
    def test_source_only_key_subset_deliberately_not_full_readiness(self):
        body=descriptor()
        for c in body['columns']:
            if c['table_name']=='tasks':c.update(default_expr="nextval('public.tasks_id_seq'::regclass)",position=3,custom_dependencies=0,sequence_refs=[['public','tasks_id_seq']])
        self.assertFalse(m.validate_timed_schema(DB(body),expected_database='fixture').source_readiness)
    def test_errors_never_echo_catalog_expression_or_driver_secret(self):
        body=descriptor();body['columns'][6]['default_expr']='private sensitive function'
        with self.assertRaises(m.TimedSchemaRefused) as err:m.validate_timed_schema(DB(body),expected_database='fixture')
        self.assertNotIn('private',str(err.exception))
        class Broken:
            def fetch_all(self,q):raise RuntimeError('secret credential')
        with self.assertRaises(m.TimedSchemaRefused) as err:m.validate_timed_schema(Broken(),expected_database='fixture')
        self.assertNotIn('secret',str(err.exception))
    def test_shadow_and_wrong_effective_resolution_refused_without_set(self):
        self.reject(lambda b:b.update(temp_shadow_count=1))
        self.reject(lambda b:b['relations'][0].update(resolves_exact=False))
        self.reject(lambda b:b.update(search_path=None))
        db=DB(descriptor());m.validate_timed_schema(db,expected_database='fixture')
        self.assertIn('pg_catalog.pg_my_temp_schema()',db.calls[0].sql)
        self.assertIn('pg_catalog.to_regclass(pg_catalog.quote_ident(c.relname))=c.oid',db.calls[0].sql)
        self.assertNotIn('SET ',db.calls[0].sql)

    def test_internal_fk_enforcement_not_just_validated_constraint(self):
        for key,value in [('enabled','D'),('type',True),('function_name','unexpected'),('deferred',True),('table_name','other')]:
            self.reject(lambda b,k=key,v=value:b['constraint_triggers'][0].update({k:v}))
        self.reject(lambda b:b['constraint_triggers'].pop())
        self.reject(lambda b:b.update(replication_role='replica'))
        self.assertIn("current_setting('session_replication_role')",m.SNAPSHOT_SQL)
        self.assertIn("OR (tn.nspname=$1 AND tc.relname=ANY($3::text[]))",m.SNAPSHOT_SQL)

    def test_bad_arguments_before_catalog_read(self):
        db=DB(descriptor())
        for kwargs in ({'expected_database':''},{'expected_database':'fixture','expected_schema':'other'},{'expected_database':'fixture','profile':'whole-db'}):
            with self.assertRaises(ValueError):m.validate_timed_schema(db,**kwargs)
        self.assertEqual(db.calls,[])

if __name__=='__main__':unittest.main()
