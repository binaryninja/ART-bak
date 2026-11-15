"""Explicit optional recording DDL, never invoked on import/startup.

Exact source db/llm_usage.go and db/commands.go DDL. Supports absent tables or
strict source-compatible tables with the five documented nullable TEXT legacy
columns absent from llm_records. Existing unexpected columns/types/defaults,
constraints, indexes, triggers, RLS, partitions or views fail closed. No drops,
data rewrite, recording enablement, database creation or production connection.
An administrator supplies an exclusive SchemaExecutor-compatible capability,
expected database and action=create_or_upgrade. Advisory lock coordinates only
cooperating writers; keep noncooperating DDL clients out during this operation.
Bodies are source TEXT, not JSONB; this setup does not change that contract.
"""
from dataclasses import dataclass
from .core import Query
from .schema import LOCK_KEY,IDENTITY_SQL,SchemaRefused,SchemaBootstrapError

USAGE_SQL='\nCREATE TABLE IF NOT EXISTS llm_usage (\n    id             BIGSERIAL PRIMARY KEY,\n    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),\n    task_id        TEXT,\n    exploration_id BIGINT,\n    worker         TEXT,\n    model          TEXT,\n    profile_name   TEXT,\n    latency_ms     INTEGER,\n    input_tokens   INTEGER NOT NULL DEFAULT 0,\n    output_tokens  INTEGER NOT NULL DEFAULT 0,\n    cache_read     INTEGER NOT NULL DEFAULT 0,\n    cache_write    INTEGER NOT NULL DEFAULT 0,\n    status         TEXT\n);\nCREATE INDEX IF NOT EXISTS idx_llm_usage_task  ON llm_usage(task_id);\nCREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(task_id, model);\nCREATE INDEX IF NOT EXISTS idx_llm_usage_exp   ON llm_usage(exploration_id);\n'
RECORDS_SQL='\nCREATE TABLE IF NOT EXISTS llm_records (\n    id            BIGSERIAL PRIMARY KEY,\n    ts            TIMESTAMPTZ DEFAULT now(),\n    model         TEXT,\n    profile_name  TEXT,\n    session_id    TEXT,\n    task_id       TEXT,\n    worker        TEXT,\n    latency_ms    INTEGER,\n    input_tokens  INTEGER,\n    output_tokens INTEGER,\n    cache_read    INTEGER,\n    cache_write   INTEGER,\n    status        TEXT,\n    error         TEXT,\n    request_body  TEXT,\n    response_body TEXT,\n    raw_request   TEXT,\n    raw_response  TEXT\n);\nCREATE INDEX IF NOT EXISTS idx_llm_records_ts ON llm_records(ts);\nCREATE INDEX IF NOT EXISTS idx_llm_records_session ON llm_records(session_id);\n'
MIGRATION_SQL='\nALTER TABLE llm_records ADD COLUMN IF NOT EXISTS task_id TEXT;\nALTER TABLE llm_records ADD COLUMN IF NOT EXISTS worker TEXT;\nALTER TABLE llm_records ADD COLUMN IF NOT EXISTS profile_name TEXT;\nALTER TABLE llm_records ADD COLUMN IF NOT EXISTS raw_request TEXT;\nALTER TABLE llm_records ADD COLUMN IF NOT EXISTS raw_response TEXT;\n'

TABLES=('llm_usage','llm_records')
OPTIONAL={'task_id','worker','profile_name','raw_request','raw_response'}
USAGE_COLUMNS=['id','ts','task_id','exploration_id','worker','model','profile_name','latency_ms','input_tokens','output_tokens','cache_read','cache_write','status']
RECORD_COLUMNS=['id','ts','model','profile_name','session_id','task_id','worker','latency_ms','input_tokens','output_tokens','cache_read','cache_write','status','error','request_body','response_body','raw_request','raw_response']
INTS={'latency_ms','input_tokens','output_tokens','cache_read','cache_write'}
INDEXES={'llm_usage':{'idx_llm_usage_task':['task_id'],'idx_llm_usage_model':['task_id','model'],'idx_llm_usage_exp':['exploration_id']},'llm_records':{'idx_llm_records_ts':['ts'],'idx_llm_records_session':['session_id']}}
# Always returns one row. Parameters are catalog data, never SQL identifiers.
INSPECT_SQL="""SELECT c.relkind,c.relrowsecurity,c.relispartition,
(SELECT jsonb_agg(jsonb_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod),'required',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid),'identity',a.attidentity,'generated',a.attgenerated) ORDER BY a.attnum)
 FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
(SELECT jsonb_agg(jsonb_build_object('name',ic.relname,'method',(SELECT amname FROM pg_am WHERE oid=ic.relam),'included',i.indnkeyatts<>i.indnatts,'options',i.indoption::text,'unique',i.indisunique,'primary',i.indisprimary,'valid',i.indisvalid,'partial',i.indpred IS NOT NULL,'expression',i.indexprs IS NOT NULL,'columns',(SELECT jsonb_agg(a.attname ORDER BY k.ord) FROM unnest(i.indkey) WITH ORDINALITY k(num,ord) LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num))) FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=c.oid),
EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal),
(SELECT array_agg(contype::text) FROM pg_constraint WHERE conrelid=c.oid)
FROM (SELECT to_regclass('public.' || $1) AS oid) wanted LEFT JOIN pg_class c ON c.oid=wanted.oid"""

def _validate(table,row,*,final=False):
    kind,rls,partition,columns,indexes,triggers,constraints=row
    if kind is None:
        if final:raise SchemaRefused('Recording table missing after setup')
        return False
    if kind!='r' or rls or partition or triggers or sorted(constraints or [])!=['p']:
        raise SchemaRefused('Unsupported recording table structure')
    expected=set(USAGE_COLUMNS if table=='llm_usage' else RECORD_COLUMNS)
    actual={c['name'] for c in columns or []}
    missing=expected-actual
    if actual-expected or (missing and (final or table!='llm_records' or not missing<=OPTIONAL)):
        raise SchemaRefused('Unsupported recording columns')
    for col in columns:
        name=col['name'];kind='bigint' if name in ('id','exploration_id') else 'timestamp with time zone' if name=='ts' else 'integer' if name in INTS else 'text'
        required=name=='id' or (table=='llm_usage' and name in {'ts','input_tokens','output_tokens','cache_read','cache_write'})
        default=col['default']
        if col['type']!=kind or col['required']!=required or col['identity'] or col['generated']:raise SchemaRefused('Unsupported recording column definition')
        expected_default='now()' if name=='ts' else '0' if required and name!='id' else None
        if name=='id':
            if default!=f"nextval('{table}_id_seq'::regclass)" and default!=f"nextval('public.{table}_id_seq'::regclass)":raise SchemaRefused('Unsupported recording ID sequence')
        elif default!=expected_default:raise SchemaRefused('Unsupported recording default')
    seen=set()
    for index in indexes or []:
        if (not index['valid'] or index['partial'] or index['expression'] or index['method']!='btree'
            or index['included'] or any(v!='0' for v in index['options'].split())):raise SchemaRefused('Unsupported recording index')
        if index['primary']:
            if index['columns']!=['id'] or not index['unique']:raise SchemaRefused('Unsupported recording primary key')
        else:
            name=index['name']
            if name not in INDEXES[table] or index['columns']!=INDEXES[table][name] or index['unique']:raise SchemaRefused('Unsupported recording index definition')
            seen.add(name)
    if final and seen!=set(INDEXES[table]):raise SchemaRefused('Recording indexes missing after setup')
    return True

@dataclass(frozen=True)
class RecordingSchemaResult:
    database:str
    tables:tuple=TABLES
    recording_enabled:bool=False


def ensure_recording_schema(executor,*,expected_database,action=None):
    if action!='create_or_upgrade':raise SchemaRefused('Explicit recording schema action required')
    if not isinstance(expected_database,str) or not expected_database.strip() or '\x00' in expected_database or expected_database in ('postgres','template0','template1'):
        raise SchemaRefused('Explicit non-maintenance database required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Recording schema target is incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            for table in TABLES:_validate(table,tx.fetch_one(Query(INSPECT_SQL,(table,))))
            tx.execute_script(USAGE_SQL)
            tx.execute_script(RECORDS_SQL)
            tx.execute_script(MIGRATION_SQL)
            for table in TABLES:_validate(table,tx.fetch_one(Query(INSPECT_SQL,(table,))),final=True)
        return RecordingSchemaResult(expected_database)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Recording schema setup failed; inspect dedicated database safely') from None
