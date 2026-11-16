"""Explicit PostgreSQL 17 extension-v1 catalog observation, never readiness.

One bounded catalog SELECT; no DDL, SET, locks, sequence advancement, data-row
reads or expression evaluation. Only five extension tables plus referenced
public.tasks/public.explorations bigint primary keys are covered. Full source
schema, permissions, ownership, graph/archive contracts and later DDL are not
certified. No setup integration or admission authority is returned.
"""
from dataclasses import dataclass
import hashlib,json
from .core import Query

class TimedSchemaRefused(RuntimeError):pass

_TABLES=('artex_task_bootstrap','artex_final_assessment','artex_final_settlement','artex_early_completion','artex_unstamped_completion')
_SEQUENCES=('artex_task_bootstrap_run_id_seq','artex_final_assessment_attempt_id_seq')
_NAMES=(*_TABLES,'tasks','explorations',*_SEQUENCES)
_MAX_BYTES=262144
# Exact PG17 deparser spelling, not general expression equivalence.
def _eq(column,value):return f"({column} = '{value}'::text)"
def _oneof(column,*values):return f"({column} = ANY (ARRAY[{', '.join(repr(v)+'::text' for v in values)}]))"

def _columns(spec):
    result=[]
    for item in spec.split(','):
        name,typ,*options=item.split(':');nullable='null' in options
        default=next((v[1:] for v in options if v.startswith('=')),None)
        result.append((name,typ,not nullable,default))
    return tuple(result)

_COMMON='task_id:int8,exploration_id:int8,run_id:int8'
_MANIFEST={
'artex_task_bootstrap':dict(columns=_columns('task_id:int8,exploration_id:int8,run_id:int8:=serial,request_id:text,config_digest:text,owner_token:text,revision:int8:=1,phase:text,goal_ids:_int8:=array,created_at:timestamptz:=now,updated_at:timestamptz:=now'),pk=('task_id',),unique=(('run_id',),),checks=(_oneof('phase','running','prepared','unresolved'),)),
'artex_final_assessment':dict(columns=_columns('attempt_id:int8:=serial,'+_COMMON+',bootstrap_revision:int8,bootstrap_identity:text,final_config:text,first_run_at:timestamptz,deadline_at:timestamptz,timeout_seconds:int8,owner_token:text,phase:text,revision:int8:=1,transcript_turn:text:null,result_digest:text:null'),pk=('attempt_id',),unique=(('task_id','run_id'),),checks=('(timeout_seconds > 0)',_oneof('phase','reserved','running','finalized'))),
'artex_final_settlement':dict(columns=_columns('attempt_id:int8,'+_COMMON+',final_revision:int8,final_identity:text,result_digest:text,graph_digest:text,status:text,completed_at:timestamptz'),pk=('attempt_id',),unique=(('task_id','run_id'),),checks=(_oneof('status','done','timeout'),)),
'artex_early_completion':dict(columns=_columns(_COMMON+',bootstrap_revision:int8,config_digest:text,first_run_at:timestamptz,deadline_at:timestamptz,timeout_seconds:int4,graph_digest:text,status:text,reason:text,completed_at:timestamptz'),pk=('task_id','run_id'),unique=(),checks=('(timeout_seconds > 0)',_eq('status','done'),_eq('reason','frontier_drained'))),
'artex_unstamped_completion':dict(columns=_columns(_COMMON+',bootstrap_revision:int8,config_digest:text,clock_state:text,timeout_seconds:int4,graph_digest:text,status:text,reason:text,completed_at:timestamptz'),pk=('task_id','run_id'),unique=(),checks=(_eq('clock_state','unstamped'),'(timeout_seconds > 0)',_eq('status','done'),_eq('reason','frontier_drained'))),
}
_MANIFEST_SHA=hashlib.sha256(json.dumps(_MANIFEST,sort_keys=True,separators=(',',':')).encode()).hexdigest()

# Each object list has a bounded projection and explicit total. Descriptors only:
# pg_get_expr deparses stored trees; it does not execute defaults or CHECKs.
SNAPSHOT_SQL="""WITH relations AS (
 SELECT c.*,n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname=$1 AND c.relname=ANY($2::text[])
), relation_rows AS (
 SELECT c.relname AS name,pg_catalog.to_regclass(pg_catalog.quote_ident(c.relname))=c.oid AS resolves_exact,c.relkind AS kind,c.relpersistence AS persistence,c.relispartition AS partition,
 c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,
 (SELECT count(*) FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid) AS inheritance,
 (SELECT count(*) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal) AS triggers,
 (SELECT count(*) FROM pg_catalog.pg_rewrite r WHERE r.ev_class=c.oid) AS rules,
 (SELECT count(*) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid) AS policies,
 (SELECT count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND a.attisdropped) AS dropped_columns
 FROM relations c
), column_rows AS (
 SELECT c.relname AS table_name,a.attname AS name,a.attnum AS position,t.typname AS type_name,tn.nspname AS type_schema,
 a.atttypmod AS typmod,a.attnotnull AS not_null,a.attidentity AS identity,a.attgenerated AS generated,a.attndims AS dimensions,
 CASE WHEN d.oid IS NULL THEN NULL WHEN pg_catalog.octet_length(pg_catalog.pg_get_expr(d.adbin,d.adrelid))<=4096
 THEN pg_catalog.pg_get_expr(d.adbin,d.adrelid) ELSE '[oversize]' END AS default_expr,
 (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(sn.nspname,s.relname))
 FROM pg_catalog.pg_depend dep JOIN pg_catalog.pg_class s ON dep.refclassid='pg_catalog.pg_class'::pg_catalog.regclass AND dep.refobjid=s.oid AND s.relkind='S'
 JOIN pg_catalog.pg_namespace sn ON sn.oid=s.relnamespace WHERE dep.classid='pg_catalog.pg_attrdef'::pg_catalog.regclass AND dep.objid=d.oid) AS sequence_refs,
 (SELECT count(*) FROM pg_catalog.pg_depend dep LEFT JOIN pg_catalog.pg_proc p ON dep.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass AND dep.refobjid=p.oid
 LEFT JOIN pg_catalog.pg_operator o ON dep.refclassid='pg_catalog.pg_operator'::pg_catalog.regclass AND dep.refobjid=o.oid
 LEFT JOIN pg_catalog.pg_namespace ns ON ns.oid=COALESCE(p.pronamespace,o.oprnamespace)
 WHERE dep.classid='pg_catalog.pg_attrdef'::pg_catalog.regclass AND dep.objid=d.oid AND ns.nspname<>'pg_catalog') AS custom_dependencies
 FROM relations c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
 JOIN pg_catalog.pg_type t ON t.oid=a.atttypid JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
 WHERE c.relname=ANY($3::text[]) OR (c.relname IN ('tasks','explorations') AND a.attname='id')
), constraint_rows AS (
 SELECT c.relname AS table_name,k.contype AS kind,k.convalidated AS validated,k.condeferrable AS deferrable,k.condeferred AS deferred,k.connoinherit AS no_inherit,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(k.conkey) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.id) AS columns,
 fn.nspname AS foreign_schema,fc.relname AS foreign_table,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(k.confkey) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.id) AS foreign_columns,
 k.confupdtype AS update_action,k.confdeltype AS delete_action,k.confmatchtype AS match_type,
 CASE WHEN k.conbin IS NULL THEN NULL WHEN pg_catalog.octet_length(pg_catalog.pg_get_expr(k.conbin,k.conrelid))<=4096
 THEN pg_catalog.pg_get_expr(k.conbin,k.conrelid) ELSE '[oversize]' END AS expression,
 (SELECT count(*) FROM pg_catalog.pg_depend dep LEFT JOIN pg_catalog.pg_proc p ON dep.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass AND dep.refobjid=p.oid
 LEFT JOIN pg_catalog.pg_operator o ON dep.refclassid='pg_catalog.pg_operator'::pg_catalog.regclass AND dep.refobjid=o.oid
 LEFT JOIN pg_catalog.pg_namespace ns ON ns.oid=COALESCE(p.pronamespace,o.oprnamespace)
 WHERE dep.classid='pg_catalog.pg_constraint'::pg_catalog.regclass AND dep.objid=k.oid AND ns.nspname<>'pg_catalog') AS custom_dependencies
 FROM relations c JOIN pg_catalog.pg_constraint k ON k.conrelid=c.oid
 LEFT JOIN pg_catalog.pg_class fc ON fc.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace fn ON fn.oid=fc.relnamespace
 WHERE c.relname=ANY($3::text[]) OR (c.relname IN ('tasks','explorations') AND k.contype='p')
), index_rows AS (
 SELECT c.relname AS table_name,i.indisprimary AS primary_key,i.indisunique AS unique_key,i.indisvalid AS valid,i.indisready AS ready,i.indimmediate AS immediate,
 i.indnkeyatts AS key_count,i.indnatts AS total_count,am.amname AS method,i.indexprs IS NULL AS no_expressions,i.indpred IS NULL AS no_predicate,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(i.indkey::smallint[]) WITH ORDINALITY x(id,n) LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.id) AS columns,
 (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(onsp.nspname,op.opcname) ORDER BY x.n) FROM pg_catalog.unnest(i.indclass::oid[]) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_opclass op ON op.oid=x.id JOIN pg_catalog.pg_namespace onsp ON onsp.oid=op.opcnamespace) AS opclasses,
 (SELECT pg_catalog.jsonb_agg(x.v ORDER BY x.n) FROM pg_catalog.unnest(i.indoption::smallint[]) WITH ORDINALITY x(v,n)) AS options,
 (SELECT pg_catalog.jsonb_agg(x.v::bigint ORDER BY x.n) FROM pg_catalog.unnest(i.indcollation::oid[]) WITH ORDINALITY x(v,n)) AS collations
 FROM relations c JOIN pg_catalog.pg_index i ON i.indrelid=c.oid JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid JOIN pg_catalog.pg_am am ON am.oid=ic.relam
 WHERE c.relname=ANY($3::text[]) OR (c.relname IN ('tasks','explorations') AND i.indisprimary)
), sequence_rows AS (
 SELECT c.relname AS name,t.typname AS type_name,s.seqstart AS start,s.seqincrement AS increment,s.seqmax AS maximum,s.seqmin AS minimum,s.seqcache AS cache,s.seqcycle AS cycle,
 (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(n.nspname,tc.relname,a.attname,d.deptype)) FROM pg_catalog.pg_depend d
 JOIN pg_catalog.pg_class tc ON d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass AND d.refobjid=tc.oid
 JOIN pg_catalog.pg_namespace n ON n.oid=tc.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid=tc.oid AND a.attnum=d.refobjsubid
 WHERE d.classid='pg_catalog.pg_class'::pg_catalog.regclass AND d.objid=c.oid AND d.deptype IN ('a','i')) AS owners
 FROM relations c JOIN pg_catalog.pg_sequence s ON s.seqrelid=c.oid JOIN pg_catalog.pg_type t ON t.oid=s.seqtypid
), constraint_trigger_rows AS (
 SELECT tn.nspname AS table_schema,tc.relname AS table_name,t.tgenabled AS enabled,t.tgtype::integer AS type,
 t.tgdeferrable AS deferrable,t.tginitdeferred AS deferred,
 pn.nspname AS function_schema,p.proname AS function_name,
 CASE WHEN pg_catalog.octet_length(t.tgargs)<=4096 THEN pg_catalog.encode(t.tgargs,'hex') ELSE '[oversize]' END AS args,
 CASE WHEN t.tgqual IS NULL THEN NULL WHEN pg_catalog.octet_length(pg_catalog.pg_get_expr(t.tgqual,t.tgrelid))<=4096 THEN pg_catalog.pg_get_expr(t.tgqual,t.tgrelid) ELSE '[oversize]' END AS condition,
 k.contype AS constraint_kind,cn.nspname AS constraint_schema,
 sn.nspname AS source_schema,sc.relname AS source_table,fn.nspname AS target_schema,fc.relname AS target_table,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(k.conkey) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.id) AS source_columns,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(k.confkey) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.id) AS target_columns,
 k.confupdtype AS update_action,k.confdeltype AS delete_action,k.confmatchtype AS match_type
 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class tc ON tc.oid=t.tgrelid JOIN pg_catalog.pg_namespace tn ON tn.oid=tc.relnamespace
 LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=k.connamespace
 LEFT JOIN pg_catalog.pg_class sc ON sc.oid=k.conrelid LEFT JOIN pg_catalog.pg_namespace sn ON sn.oid=sc.relnamespace
 LEFT JOIN pg_catalog.pg_class fc ON fc.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace fn ON fn.oid=fc.relnamespace
 JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
 WHERE t.tgisinternal AND ((sn.nspname=$1 AND sc.relname=ANY($3::text[])) OR (tn.nspname=$1 AND tc.relname=ANY($3::text[])))
), snapshot AS (SELECT pg_catalog.jsonb_build_object(
 'database',pg_catalog.current_database(),'schema',$1::text,'replication_role',pg_catalog.current_setting('session_replication_role'),
 'temp_shadow_count',(SELECT count(*) FROM pg_catalog.pg_class c WHERE c.relnamespace=pg_catalog.pg_my_temp_schema() AND c.relname=ANY($2::text[])),
 'search_path',CASE WHEN pg_catalog.octet_length(pg_catalog.current_setting('search_path'))<=4096 THEN pg_catalog.current_setting('search_path') ELSE NULL END,'version',pg_catalog.current_setting('server_version_num')::integer,
 'relations_count',(SELECT count(*) FROM relation_rows),'relations',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM relation_rows ORDER BY name LIMIT 10) x),
 'columns_count',(SELECT count(*) FROM column_rows),'columns',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM column_rows ORDER BY table_name,position LIMIT 128) x),
 'constraints_count',(SELECT count(*) FROM constraint_rows),'constraints',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM constraint_rows ORDER BY table_name,kind LIMIT 64) x),
 'indexes_count',(SELECT count(*) FROM index_rows),'indexes',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM index_rows ORDER BY table_name,primary_key LIMIT 32) x),
 'constraint_triggers_count',(SELECT count(*) FROM constraint_trigger_rows),'constraint_triggers',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM constraint_trigger_rows ORDER BY source_table,table_name,function_name LIMIT 512) x),
 'sequences_count',(SELECT count(*) FROM sequence_rows),'sequences',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM sequence_rows ORDER BY name LIMIT 3) x)) AS body)
SELECT CASE WHEN pg_catalog.octet_length(body::text)<=$4 THEN body ELSE NULL END FROM snapshot"""

@dataclass(frozen=True)
class TimedSchemaObservation:
    database:str
    schema:str
    server_major:int
    profile:str
    manifest_digest:str
    descriptor_digest:str
    tables:tuple
    source_readiness:bool=False

def _refuse(category):raise TimedSchemaRefused('Timed schema incompatible: '+category)
def _require(condition,category):
    if not condition:_refuse(category)
def _group(items,key):
    out={}
    for item in items:
        _require(type(item) is dict and type(item.get(key)) is str,'descriptor')
        out.setdefault(item[key],[]).append(item)
    return out

def _expected_fk_triggers():
    expected_triggers=[]
    for name in _TABLES:
        refs=[('task_id','tasks','id'),('exploration_id','explorations','id')]
        if name=='artex_final_settlement':refs.append(('attempt_id','artex_final_assessment','attempt_id'))
        for column,target,key in refs:
            base=dict(table_schema='public',enabled='O',deferrable=False,deferred=False,function_schema='pg_catalog',args='',condition=None,constraint_kind='f',constraint_schema='public',source_schema='public',source_table=name,target_schema='public',target_table=target,source_columns=[column],target_columns=[key],update_action='a',delete_action='a',match_type='s')
            for table,typ,fn in ((name,5,'RI_FKey_check_ins'),(name,17,'RI_FKey_check_upd'),(target,9,'RI_FKey_noaction_del'),(target,17,'RI_FKey_noaction_upd')):
                expected_triggers.append(dict(base,table_name=table,type=typ,function_name=fn))
    return expected_triggers

def validate_timed_schema(executor,*,expected_database,expected_schema='public',profile='extension-v1'):
    if type(expected_database) is not str or not expected_database or len(expected_database.encode())>256 or '\0' in expected_database or expected_schema!='public' or profile!='extension-v1':
        raise ValueError('Explicit database/public and extension-v1 required')
    if not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit catalog reader required')
    try:
        rows=executor.fetch_all(Query(SNAPSHOT_SQL,(expected_schema,list(_NAMES),list(_TABLES),_MAX_BYTES)))
        _require(type(rows) in (list,tuple) and len(rows)==1 and type(rows[0]) in (list,tuple) and len(rows[0])==1,'snapshot')
        body=rows[0][0];_require(type(body) is dict,'snapshot')
        _require(body.get('database')==expected_database and body.get('schema')==expected_schema,'database identity')
        _require(type(body.get('temp_shadow_count')) is int and body['temp_shadow_count']==0 and type(body.get('search_path')) is str,'session resolution')
        _require(body.get('replication_role')=='origin','constraint enforcement session')
        version=body.get('version');_require(type(version) is int and 170000<=version<180000,'PostgreSQL major')
        for key,limit in (('relations',10),('columns',128),('constraints',64),('indexes',32),('sequences',3),('constraint_triggers',512)):
            count=body.get(key+'_count');items=body.get(key)
            _require(type(count) is int and type(items) is list and len(items)==count<=limit,'bounded '+key)
        numeric={'relations':('inheritance','triggers','rules','policies','dropped_columns'),'columns':('position','typmod','dimensions','custom_dependencies'),'constraints':('custom_dependencies',),'indexes':('key_count','total_count'),'sequences':('start','increment','maximum','minimum','cache')}
        for key,fields in numeric.items():
            for row in body[key]:
                _require(type(row) is dict and all(type(row.get(f)) is int for f in fields),'numeric descriptor')
        for row in body['indexes']:
            _require(type(row.get('primary_key')) is bool and type(row.get('options')) is list and type(row.get('collations')) is list and all(type(v) is int for v in row['options']+row['collations']),'index scalar descriptor')
        relations=_group(body['relations'],'name');_require(set(relations)==set(_NAMES),'relation inventory')
        for name,items in relations.items():
            _require(len(items)==1,'relation uniqueness');r=items[0]
            _require(r.get('resolves_exact') is True and r['kind']==('S' if name in _SEQUENCES else 'r') and r['persistence']=='p' and r['partition'] is False and r['inheritance']==0,'relation kind')
            if name in _TABLES:
                _require(r['rls'] is False and r['force_rls'] is False and all(type(r[k]) is int and r[k]==0 for k in ('triggers','rules','policies','dropped_columns')),'extension behavior')
        columns=_group(body['columns'],'table_name');_require(set(columns)==set(_TABLES)|{'tasks','explorations'},'column inventory')
        for name,items in columns.items():
            spec=_MANIFEST[name]['columns'] if name in _MANIFEST else (('id','int8',True,None),)
            _require(len(items)==len(spec),'column count')
            for number,(c,(column,typ,nonnull,default)) in enumerate(zip(items,spec),1):
                _require(c['name']==column and c['type_schema']=='pg_catalog' and c['type_name']==typ and c['typmod']==-1 and c['not_null'] is nonnull and c['identity']=='' and c['generated']=='' and c['dimensions']==(1 if typ=='_int8' else 0),'column shape')
                if name not in _MANIFEST:continue # Source id default/physical position outside this profile.
                _require(c['position']==number and c['custom_dependencies']==0,'column order/dependencies')
                value=c['default_expr'];refs=c['sequence_refs']
                if default=='serial':
                    sequence=name+'_'+column+'_seq'
                    _require(value in (f"nextval('{sequence}'::regclass)",f"nextval('public.{sequence}'::regclass)") and refs==[['public',sequence]],'serial default')
                else:
                    wanted={'1':'1','array':"'{}'::bigint[]",'now':'now()',None:None}[default]
                    _require(value==wanted and refs is None,'column default')
        constraints=_group(body['constraints'],'table_name');indexes=_group(body['indexes'],'table_name')
        _require(set(constraints)==set(columns) and set(indexes)==set(columns),'keys inventory')
        for name in columns:
            spec=_MANIFEST.get(name,dict(pk=('id',),unique=(),checks=()))
            expected_keys=[('p',spec['pk']),*(('u',key) for key in spec['unique'])]
            actual_keys=[];checks=[];foreign=[]
            for c in constraints[name]:
                _require(c['validated'] is True and c['deferrable'] is False and c['deferred'] is False and c['no_inherit'] is (c['kind'] in ('p','u','f')) and c['custom_dependencies']==0,'constraint flags')
                if c['kind'] in ('p','u'):actual_keys.append((c['kind'],tuple(c['columns'] or ())))
                elif c['kind']=='c':checks.append(c['expression'])
                elif c['kind']=='f':
                    _require(c['foreign_schema']=='public' and c['update_action']=='a' and c['delete_action']=='a' and c['match_type']=='s','foreign key flags')
                    foreign.append((tuple(c['columns']),c['foreign_table'],tuple(c['foreign_columns'])))
                else:_refuse('constraint kind')
            expected_foreign=[] if name not in _MANIFEST else [(('task_id',),'tasks',('id',)),(('exploration_id',),'explorations',('id',))]
            if name=='artex_final_settlement':expected_foreign.append((('attempt_id',),'artex_final_assessment',('attempt_id',)))
            _require(sorted(actual_keys)==sorted(expected_keys) and sorted(checks)==sorted(spec['checks']) and sorted(foreign)==sorted(expected_foreign),'constraint contract')
            expected_indexes=sorted((kind=='p',key) for kind,key in expected_keys);actual_indexes=[]
            for i in indexes[name]:
                keys=tuple(i['columns'] or ());n=len(keys)
                _require(all(i[k] is True for k in ('unique_key','valid','ready','immediate','no_expressions','no_predicate')) and i['method']=='btree' and i['key_count']==i['total_count']==n and i['opclasses']==[['pg_catalog','int8_ops']]*n and i['options']==[0]*n and i['collations']==[0]*n,'index contract')
                actual_indexes.append((i['primary_key'],keys))
            _require(sorted(actual_indexes)==expected_indexes,'index inventory')
        expected_triggers=_expected_fk_triggers()
        encode=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
        _require(sorted(map(encode,body['constraint_triggers']))==sorted(map(encode,expected_triggers)),'foreign key enforcement')
        seqs=_group(body['sequences'],'name');_require(set(seqs)==set(_SEQUENCES),'sequence inventory')
        for name,items in seqs.items():
            _require(len(items)==1,'sequence uniqueness');s=items[0]
            table,column=('artex_task_bootstrap','run_id') if name==_SEQUENCES[0] else ('artex_final_assessment','attempt_id')
            _require(s['type_name']=='int8' and s['start']==s['increment']==s['minimum']==s['cache']==1 and s['maximum']==2**63-1 and s['cycle'] is False and s['owners']==[['public',table,column,'a']],'sequence contract')
        canonical=dict(body)
        for key in ('relations','columns','constraints','indexes','sequences','constraint_triggers'):
            canonical[key]=sorted(body[key],key=lambda row:json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False))
        encoded=json.dumps(canonical,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        _require(len(encoded)<=_MAX_BYTES,'snapshot bytes')
        return TimedSchemaObservation(expected_database,expected_schema,17,profile,_MANIFEST_SHA,hashlib.sha256(encoded).hexdigest(),_TABLES)
    except TimedSchemaRefused:raise
    except Exception:raise TimedSchemaRefused('Timed schema observation unavailable') from None
