"""Strict, read-only PG17 source-contract observation for a restricted candidate.

The expected descriptor is code-owned, captured from vetted original source DDL,
not learned from the inspected database. No admission capability, migration,
expression evaluation, table data, credential read, or SET is performed here.
"""
from dataclasses import dataclass
import hashlib,json
from pathlib import Path
from .core import Query
from .timed_schema import _expected_fk_triggers

_TABLES=('tasks','explorations','exploration_nodes','exploration_edges','exploration_anchors',
 'task_archives','task_relations','task_scope','task_categories','llm_profiles',
 'task_llm_profiles','agents','agent_prompts','companies','assets','task_asset_links')
# Tables without a serial identity use composite keys.
_SEQUENCES=tuple(name+'_id_seq' for name in _TABLES if name not in
 ('exploration_edges','exploration_anchors','task_relations','task_llm_profiles','task_asset_links'))
_FUNCTIONS=('set_updated_at','try_inet','sync_task_asset_links')
_NAMES=(*_TABLES,*_SEQUENCES)
_LIMITS={'relations':64,'columns':512,'constraints':256,'indexes':256,'sequences':32,'triggers':64,'functions':8,'constraint_triggers':512}
_MAX_BYTES=1048576
# Replaced only after independent baseline review, never from caller/target input.
_BASELINE_SHA='a9975df8e2b05f5a02bc2c4e25be254569fa3e254785d25364836fa76d26ee67'
_SOURCE_SHA256='faaf0116e85c7ccd949139fec36ec876c537237098456e588fcd7afddce899f5'

class TimedSourceSchemaRefused(RuntimeError):pass

@dataclass(frozen=True)
class TimedSourceObservation:
    database:str
    schema:str
    server_major:int
    profile:str
    manifest_digest:str
    descriptor_digest:str
    tables:tuple
    modes:tuple=('local-goals-planner-worker','cleanup','clock','final-lifecycle')
    source_readiness:bool=False

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
 CASE WHEN a.attcollation=0 THEN NULL ELSE (SELECT pg_catalog.jsonb_build_array(ns.nspname,col.collname,col.collprovider,col.collisdeterministic,col.collencoding,col.collcollate,col.collctype,col.colllocale,col.collversion) FROM pg_catalog.pg_collation col JOIN pg_catalog.pg_namespace ns ON ns.oid=col.collnamespace WHERE col.oid=a.attcollation) END AS collation,
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
 WHERE c.relname=ANY($3::text[])
), constraint_rows AS (
 SELECT c.relname AS table_name,k.conname AS name,k.contype AS kind,k.convalidated AS validated,k.condeferrable AS deferrable,k.condeferred AS deferred,k.connoinherit AS no_inherit,
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
 WHERE c.relname=ANY($3::text[])
), index_rows AS (
 SELECT c.relname AS table_name,ic.relname AS name,i.indisprimary AS primary_key,i.indisunique AS unique_key,i.indisvalid AS valid,i.indisready AS ready,i.indimmediate AS immediate,
 i.indnkeyatts AS key_count,i.indnatts AS total_count,am.amname AS method,i.indnullsnotdistinct AS nulls_not_distinct,
 CASE WHEN pg_catalog.octet_length(pg_catalog.pg_get_indexdef(i.indexrelid))<=16384 THEN pg_catalog.pg_get_indexdef(i.indexrelid) ELSE '[oversize]' END AS definition,
 (SELECT count(*) FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_proc pr ON d.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass AND d.refobjid=pr.oid JOIN pg_catalog.pg_namespace ns ON ns.oid=pr.pronamespace WHERE d.classid='pg_catalog.pg_class'::pg_catalog.regclass AND d.objid=i.indexrelid AND ns.nspname<>'pg_catalog') AS custom_functions,
 i.indexprs IS NULL AS no_expressions,i.indpred IS NULL AS no_predicate,
 (SELECT pg_catalog.jsonb_agg(a.attname ORDER BY x.n) FROM pg_catalog.unnest(i.indkey::smallint[]) WITH ORDINALITY x(id,n) LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.id) AS columns,
 (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(onsp.nspname,op.opcname) ORDER BY x.n) FROM pg_catalog.unnest(i.indclass::oid[]) WITH ORDINALITY x(id,n) JOIN pg_catalog.pg_opclass op ON op.oid=x.id JOIN pg_catalog.pg_namespace onsp ON onsp.oid=op.opcnamespace) AS opclasses,
 (SELECT pg_catalog.jsonb_agg(x.v ORDER BY x.n) FROM pg_catalog.unnest(i.indoption::smallint[]) WITH ORDINALITY x(v,n)) AS options,
 (SELECT pg_catalog.jsonb_agg(CASE WHEN x.v=0 THEN NULL ELSE pg_catalog.jsonb_build_array(ns.nspname,col.collname,col.collprovider,col.collisdeterministic,col.collencoding,col.collcollate,col.collctype,col.colllocale,col.collversion) END ORDER BY x.n) FROM pg_catalog.unnest(i.indcollation::oid[]) WITH ORDINALITY x(v,n) LEFT JOIN pg_catalog.pg_collation col ON col.oid=x.v LEFT JOIN pg_catalog.pg_namespace ns ON ns.oid=col.collnamespace) AS collations
 FROM relations c JOIN pg_catalog.pg_index i ON i.indrelid=c.oid JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid JOIN pg_catalog.pg_am am ON am.oid=ic.relam
 WHERE c.relname=ANY($3::text[])
), sequence_rows AS (
 SELECT c.relname AS name,t.typname AS type_name,s.seqstart AS start,s.seqincrement AS increment,s.seqmax AS maximum,s.seqmin AS minimum,s.seqcache AS cache,s.seqcycle AS cycle,
 (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(n.nspname,tc.relname,a.attname,d.deptype)) FROM pg_catalog.pg_depend d
 JOIN pg_catalog.pg_class tc ON d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass AND d.refobjid=tc.oid
 JOIN pg_catalog.pg_namespace n ON n.oid=tc.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid=tc.oid AND a.attnum=d.refobjsubid
 WHERE d.classid='pg_catalog.pg_class'::pg_catalog.regclass AND d.objid=c.oid AND d.deptype IN ('a','i')) AS owners
 FROM relations c JOIN pg_catalog.pg_sequence s ON s.seqrelid=c.oid JOIN pg_catalog.pg_type t ON t.oid=s.seqtypid
), trigger_rows AS (
 SELECT c.relname AS table_name,t.tgname AS name,t.tgenabled AS enabled,t.tgtype::integer AS type,
 t.tgdeferrable AS deferrable,t.tginitdeferred AS deferred,ns.nspname AS function_schema,p.proname AS function_name,
 pg_catalog.pg_get_function_identity_arguments(p.oid) AS function_args,
 CASE WHEN pg_catalog.octet_length(pg_catalog.pg_get_triggerdef(t.oid))<=16384 THEN pg_catalog.pg_get_triggerdef(t.oid) ELSE '[oversize]' END AS definition
 FROM relations c JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid AND NOT t.tgisinternal
 JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
), function_rows AS (
 SELECT p.proname AS name,pg_catalog.pg_get_function_identity_arguments(p.oid) AS args,
 pg_catalog.pg_function_is_visible(p.oid) AS visible,l.lanname AS language,p.prokind AS kind,
 p.provolatile AS volatility,p.proparallel AS parallel,p.proisstrict AS strict,p.prosecdef AS security_definer,p.proleakproof AS leakproof,
 p.proretset AS returns_set,p.proconfig AS config,pg_catalog.pg_get_function_result(p.oid) AS result,
 CASE WHEN pg_catalog.octet_length(p.prosrc)<=16384 THEN p.prosrc ELSE '[oversize]' END AS body,
 CASE WHEN pg_catalog.octet_length(pg_catalog.pg_get_functiondef(p.oid))<=32768 THEN pg_catalog.pg_get_functiondef(p.oid) ELSE '[oversize]' END AS definition,
 (SELECT count(*) FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass AND d.refobjid=e.oid WHERE d.classid='pg_catalog.pg_proc'::pg_catalog.regclass AND d.objid=p.oid) AS extension_dependencies
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_language l ON l.oid=p.prolang WHERE n.nspname=$1 AND p.proname=ANY($5::text[])
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
 WHERE t.tgisinternal AND (t.tgrelid IN (SELECT oid FROM relations) OR (sn.nspname=$1 AND sc.relname=ANY($3::text[])))
), snapshot AS (SELECT pg_catalog.jsonb_build_object(
 'database',pg_catalog.current_database(),'schema',$1::text,'replication_role',pg_catalog.current_setting('session_replication_role'),'effective_schemas',pg_catalog.current_schemas(true),
 'database_locale',(SELECT pg_catalog.jsonb_build_object('encoding',pg_catalog.pg_encoding_to_char(d.encoding),'provider',d.datlocprovider,'collate',d.datcollate,'ctype',d.datctype,'locale',pg_catalog.to_jsonb(d)->>'datlocale','rules',pg_catalog.to_jsonb(d)->>'daticurules','version',d.datcollversion) FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()),
 'temp_shadow_count',(SELECT count(*) FROM pg_catalog.pg_class c WHERE c.relnamespace=pg_catalog.pg_my_temp_schema() AND c.relname=ANY($2::text[])),
 'search_path',CASE WHEN pg_catalog.octet_length(pg_catalog.current_setting('search_path'))<=4096 THEN pg_catalog.current_setting('search_path') ELSE NULL END,'version',pg_catalog.current_setting('server_version_num')::integer,
 'relations_count',(SELECT count(*) FROM relation_rows),'relations',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM relation_rows ORDER BY name LIMIT 64) x),
 'columns_count',(SELECT count(*) FROM column_rows),'columns',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM column_rows ORDER BY table_name,position LIMIT 512) x),
 'constraints_count',(SELECT count(*) FROM constraint_rows),'constraints',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM constraint_rows ORDER BY table_name,kind LIMIT 256) x),
 'indexes_count',(SELECT count(*) FROM index_rows),'indexes',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM index_rows ORDER BY table_name,primary_key LIMIT 256) x),
 'triggers_count',(SELECT count(*) FROM trigger_rows),'triggers',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM trigger_rows ORDER BY table_name,name LIMIT 64) x),
 'functions_count',(SELECT count(*) FROM function_rows),'functions',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM function_rows ORDER BY name,args LIMIT 8) x),
 'constraint_triggers_count',(SELECT count(*) FROM constraint_trigger_rows),'constraint_triggers',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM constraint_trigger_rows ORDER BY source_table,table_name,function_name LIMIT 512) x),
 'sequences_count',(SELECT count(*) FROM sequence_rows),'sequences',(SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM sequence_rows ORDER BY name LIMIT 32) x)) AS body)
SELECT CASE WHEN pg_catalog.octet_length(body::text)<=$4 THEN body ELSE NULL END FROM snapshot"""


def _canonical(body):
    return json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def _shape(value,depth=0):
    if depth>24:raise TimedSourceSchemaRefused('Source descriptor depth unsupported')
    if type(value) in (str,int,bool,type(None)):
        if type(value) is str:value.encode('utf-8')
        return
    if type(value) is list:
        for item in value:_shape(item,depth+1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():_shape(item,depth+1)
        return
    raise TimedSourceSchemaRefused('Source descriptor type unsupported')

def _contract(body):
    """Only session DB/version fields vary; actual semantic rows remain exact."""
    if type(body.get('database_locale')) is not dict:raise TimedSourceSchemaRefused('Source locale unavailable')
    out={'database_locale':body['database_locale']}
    for key,limit in _LIMITS.items():
        rows=body.get(key);count=body.get(key+'_count')
        if type(count) is not int or type(rows) is not list or not 0<=count<=limit or len(rows)!=count:
            raise TimedSourceSchemaRefused('Source descriptor bounds unsupported')
        if any(type(row) is not dict for row in rows):raise TimedSourceSchemaRefused('Source descriptor row unsupported')
        out[key]=sorted(rows,key=_canonical)
    return out

def validate_timed_source_schema(executor,*,expected_database,expected_schema='public',profile='task-runtime-v1'):
    if type(expected_database) is not str or not expected_database or len(expected_database.encode())>256 or '\0' in expected_database or expected_schema!='public' or profile!='task-runtime-v1':
        raise ValueError('Explicit database/public and task-runtime-v1 required')
    if not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit catalog reader required')
    try:
        # Never accept caller-supplied fingerprints or generate this baseline here.
        raw=Path(__file__).with_name('timed_source_manifest.json').read_bytes()
        if len(raw)>_MAX_BYTES or hashlib.sha256(raw).hexdigest()!=_BASELINE_SHA:
            raise TimedSourceSchemaRefused('Reviewed source manifest unavailable')
        manifest=json.loads(raw);_shape(manifest)
        expected=manifest['contract']
        if manifest['profile']!='task-runtime-v1' or manifest['provenance']['source_sha256']!=_SOURCE_SHA256 or manifest['provenance']['server_major']!=17:
            raise TimedSourceSchemaRefused('Source manifest provenance incompatible')
        rows=executor.fetch_all(Query(SNAPSHOT_SQL,(expected_schema,list(_NAMES),list(_TABLES),_MAX_BYTES,list(_FUNCTIONS))))
        if type(rows) not in (list,tuple) or len(rows)!=1 or type(rows[0]) not in (list,tuple) or len(rows[0])!=1 or type(rows[0][0]) is not dict:
            raise TimedSourceSchemaRefused('Source catalog snapshot unavailable')
        body=rows[0][0];_shape(body)
        if len(_canonical(body))>_MAX_BYTES:raise TimedSourceSchemaRefused('Source snapshot exceeds bounds')
        if body.get('database')!=expected_database or body.get('schema')!='public' or type(body.get('version')) is not int or not 170000<=body['version']<180000:
            raise TimedSourceSchemaRefused('Source database/version incompatible')
        if body.get('replication_role')!='origin':raise TimedSourceSchemaRefused('Source enforcement session incompatible')
        if type(body.get('temp_shadow_count')) is not int or body['temp_shadow_count']!=0 or body.get('effective_schemas')!=['pg_catalog','public']:
            raise TimedSourceSchemaRefused('Source resolution incompatible')
        # JSON bytes distinguish integer, boolean and string catalog representations.
        # Pristine source baseline remains unchanged; required receipt tables add
        # exactly their known parent-side RI actions on tasks/explorations.
        # The companion extension validator independently requires all five tables.
        expected=dict(expected)
        additions=[row for row in _expected_fk_triggers() if row['table_name'] in ('tasks','explorations')]
        expected['constraint_triggers']=sorted(expected['constraint_triggers']+additions,key=_canonical)
        contract=_canonical(_contract(body))
        if contract!=_canonical(expected):raise TimedSourceSchemaRefused('Source contract incompatible')
        return TimedSourceObservation(expected_database,'public',17,profile,_BASELINE_SHA,hashlib.sha256(contract).hexdigest(),_TABLES)
    except TimedSourceSchemaRefused:raise
    except Exception:raise TimedSourceSchemaRefused('Source schema observation unavailable') from None
