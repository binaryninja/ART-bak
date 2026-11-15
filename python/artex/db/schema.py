"""Explicit, fresh-database-only PostgreSQL schema bootstrap.

This is NOT db.Open parity or an existing-database migration runner. It does not
create databases, seed built-in agents/prompt variables/visibility/safety rules,
apply seed migrations, initialize optional llm_records/llm_usage tables, start
services, or configure credentials. The SQL includes only schema.sql's own seeds
(e.g. disabled ScopeSentry placeholder). A complete runnable deployment requires
the separately ported seed/catalog/configuration stages; never bypass them.

An administrator must supply an exclusive, idle connection to a fresh empty
PostgreSQL 12+ database with an empty public schema. Existing user objects or
additional user schemas/extensions fail closed. The exact expected database name
and allow_schema_creation=True are mandatory. Never invoked automatically.
The advisory transaction lock uses upstream key 7337741001 and coordinates with
upstream startup/session locks and archive locks. Non-cooperating external DDL
cannot be prevented by this lock: keep other users out during bootstrap.

Packaged SQL is upstream db/schema.sql with translated comments and four legacy
text values expressed as U& Unicode literals without changing decoded values.
A pinned digest prevents silent partial/modified scripts. Existing upgrade paths
inside the copied SQL are inert on a fresh DB; rerunning bootstrap is rejected.

SchemaExecutor is a dedicated pinned transaction adapter, separate from the DML
PostgresExecutor row-count contract. Its injected cursor factory must return a
native PostgreSQL $n-bind cursor supporting multi-statement scripts when execute
receives SQL without parameters (e.g. psycopg RawCursor). It must propagate errors
from ALL statements. No SQL splitting, placeholder rewriting, driver installation,
connection creation, credentials, or source-program execution occurs on import.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import re
from threading import RLock
from .core import Query

LOCK_KEY=7337741001
SCHEMA_SHA256='faaf0116e85c7ccd949139fec36ec876c537237098456e588fcd7afddce899f5'


class SchemaRefused(RuntimeError):pass
class SchemaBootstrapError(RuntimeError):pass


@dataclass(frozen=True)
class BootstrapResult:
    database: str
    table_count: int
    schema_sha256: str
    builtins_seeded: bool = False
    existing_database_upgrades_supported: bool = False


def schema_sql():
    raw=files('artex.db').joinpath('schema.sql').read_bytes()
    if sha256(raw).hexdigest()!=SCHEMA_SHA256:
        raise SchemaRefused('Packaged schema integrity check failed')
    return raw.decode('utf-8')


class SchemaExecutor:
    """Borrow one exclusive connection; caller owns its eventual close.

    cursor_factory(connection) -> context-managed native-bind tuple cursor.
    Construction neither connects nor modifies transaction/autocommit settings.
    """
    def __init__(self,connection,*,cursor_factory):
        if not callable(cursor_factory):raise TypeError('a native-bind cursor factory is required')
        if not connection.autocommit or connection.info.transaction_status!=0:
            raise SchemaRefused('Bootstrap requires an idle autocommit connection')
        self._connection=connection;self._factory=cursor_factory;self._active=False;self._lock=RLock()

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._active or self._connection.closed or self._connection.info.transaction_status!=0:
                raise SchemaRefused('Bootstrap requires an exclusive idle connection')
            with self._connection.transaction():
                self._active=True
                try:yield self
                finally:self._active=False

    def _check(self):
        if not self._active:raise SchemaRefused('Schema operations require an explicit transaction')

    def fetch_one(self,query):
        with self._lock:
            self._check()
            if not isinstance(query,Query):raise TypeError('expected Query')
            with self._factory(self._connection) as cursor:
                cursor.execute(query.sql,query.params)
                row=cursor.fetchone()
                if row is None:raise SchemaBootstrapError('Required schema query returned no row')
                return row

    def execute_script(self,sql):
        with self._lock:
            self._check()
            if not isinstance(sql,str):raise TypeError('script must be SQL text')
            with self._factory(self._connection) as cursor:
                # Deliberately no params argument: preserve DO/function bodies
                # and let the driver use its multi-statement script protocol.
                cursor.execute(sql)
                # DDL rowcount is normally -1 and is not a failure.


IDENTITY_SQL="""SELECT current_database(), current_setting('server_version_num')::integer,
current_setting('transaction_read_only'), current_setting('standard_conforming_strings'), pg_my_temp_schema()"""
EMPTY_SQL="""SELECT
EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='public'),
EXISTS(
 SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname !~ '^pg_'
 UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
 UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL SELECT 1 FROM pg_extension WHERE extname<>'plpgsql'
)"""
TABLES_SQL="""SELECT array_agg(c.relname ORDER BY c.relname) FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'"""


def bootstrap_empty_database(executor,*,expected_database,allow_schema_creation=False):
    """Atomically apply packaged DDL only after strict fresh-database checks.

    Success means schema creation, not a fully seeded application. All failures,
    including post-DDL validation and commit errors, propagate as safe errors.
    Error messages never include connection details or SQL bind values.
    """
    if allow_schema_creation is not True:raise SchemaRefused('Explicit schema-creation opt-in is required')
    if (not isinstance(expected_database,str) or not expected_database.strip()
        or '\x00' in expected_database or expected_database in ('postgres','template0','template1')):
        raise SchemaRefused('A non-maintenance target database name is required')
    script=schema_sql()
    expected_tables=sorted(re.findall(r'^CREATE TABLE IF NOT EXISTS ([a-z_]+)\s*\(',script,re.M))
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,read_only,standard_strings,temp_schema=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database:raise SchemaRefused('Connected database does not match the expected target')
            if type(version) is not int or version<120000:raise SchemaRefused('Fresh bootstrap requires PostgreSQL 12 or later')
            if read_only!='off' or standard_strings!='on' or temp_schema!=0:
                raise SchemaRefused('Bootstrap requires writable standard-string mode without a temporary schema')
            has_public,has_objects=tx.fetch_one(Query(EMPTY_SQL))
            if has_public is not True or has_objects is not False:
                raise SchemaRefused('Only an empty public schema in an otherwise empty user database is supported; upgrades are unavailable')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            tx.execute_script(script)
            actual=tx.fetch_one(Query(TABLES_SQL))[0]
            if not isinstance(actual,(list,tuple)) or sorted(actual)!=expected_tables:
                raise SchemaBootstrapError('Created schema table manifest does not match the packaged schema')
        return BootstrapResult(expected_database,len(expected_tables),SCHEMA_SHA256)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Schema bootstrap failed; inspect the dedicated database safely') from None
