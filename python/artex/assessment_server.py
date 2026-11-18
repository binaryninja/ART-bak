"""Local launcher for registered assessments on the shared task engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
from wsgiref.simple_server import make_server
from .__main__ import _configuration
from .agent.bootstrap import DDL as JOURNAL_DDL
from .agent.tool_ledger import DDL as TOOL_LEDGER_DDL
from .assessments.credentials import CredentialStore
from .assessments.store import DDL,AssessmentStore
from .assessments.runtime import AssessmentRuntime,DOMAIN_CATALOG,FRAMEWORK_CATALOG
from .assessments.extensions import ExtensionHost
from .db.pool import ExecutorPool
from .db.profiles import ProfileStore
from .db.core import Query
from .server.assessments import register
from .server.passwords import BcryptCodec
from .server.runtime_listener import DevListenerConfig,QuietRuntimeHandler,_BoundedServer
from .servicenow_recon import LOCK_KEY,private_file

def install(connection,extra_catalog=()):
    with connection.transaction():
        connection.execute(DDL)
        connection.execute(TOOL_LEDGER_DDL)
        if connection.execute("SELECT to_regclass('public.artex_task_bootstrap')").fetchone()[0] is None:connection.execute(JOURNAL_DDL)
        for role in ('goals','planner','worker'):
            connection.execute('INSERT INTO agents(key,name,role,builtin,enabled) VALUES(%s,%s,%s,true,true) ON CONFLICT(key) DO NOTHING',(role,role.title(),role))
        for tool in tuple(FRAMEWORK_CATALOG)+tuple(DOMAIN_CATALOG)+tuple(extra_catalog):
            connection.execute("INSERT INTO tools(key,system,description,schema,agents,enabled,kind,exec,deferred) VALUES(%s,true,%s,%s::jsonb,%s::jsonb,true,'builtin','{}'::jsonb,%s) ON CONFLICT(key) DO NOTHING",(tool.key,tool.description,json.dumps(tool.schema),json.dumps(tool.agents),tool.deferred))
            connection.execute("UPDATE tools SET schema=%s::jsonb WHERE key=%s AND system AND kind='builtin' AND schema->'properties' IS DISTINCT FROM %s::jsonb->'properties'",(json.dumps(tool.schema),tool.key,json.dumps(tool.schema)))

def migrate_legacy(pool):
    from .server.servicenow_recon import DESCRIPTION,GOAL
    from .recon.servicenow import ORIGIN
    with pool.transaction() as tx:
        rows=tx.fetch_all(Query("SELECT id,exploration_id,status FROM tasks WHERE description=$1 AND goal=$2 AND status IN ('done','failed','timeout')",(DESCRIPTION,GOAL)))
        for task_id,exploration_id,status in rows:
            identifier='legacy-servicenow-'+str(task_id)
            tx.execute(Query("INSERT INTO artex_assessments(id,dedup_key,definition,version,origin,task_id,state) VALUES($1,$1,'servicenow.inventory',0,$2,$3,$4) ON CONFLICT DO NOTHING",(identifier,ORIGIN,task_id,status)))
            for kind,detail in tx.fetch_all(Query("SELECT kind,detail FROM activity WHERE exploration_id=$1 AND worker='servicenow-recon' AND kind IN ('recon_manifest','recon_observation','recon_report') ORDER BY id",(exploration_id,))):
                value=json.loads(detail)
                if kind=='recon_observation':tx.execute(Query('INSERT INTO artex_assessment_observations(assessment_id,operation,result) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING',(identifier,value['resource'],json.dumps(value))))
                elif kind=='recon_report':tx.execute(Query('UPDATE artex_assessments SET report=$2::jsonb WHERE id=$1',(identifier,json.dumps({'summary':value.get('report',''),'legacy':value,'capabilities':{'authenticated':value.get('verified',False),'access_level':'unknown'}}))))

def reconcile_before_inventory(pool,task_ids):
    """Explicit recovery under the launcher's exclusive database owner lock."""
    for task_id in task_ids:
        with pool.transaction() as tx:
            row=tx.fetch_one(Query("SELECT a.id,t.exploration_id,t.status FROM artex_assessments a JOIN tasks t ON t.id=a.task_id WHERE t.id=$1 AND a.version=1 FOR UPDATE OF a,t",(task_id,)))
            if row is None or row[2]!='running':raise RuntimeError('Recovery task is not an abandoned assessment')
            identifier,exploration_id,_=row
            if tx.fetch_all(Query('SELECT 1 FROM artex_assessment_observations WHERE assessment_id=$1 LIMIT 1',(identifier,))):raise RuntimeError('Inventory effects require separate reconciliation')
            if tx.fetch_all(Query("SELECT 1 FROM llm_records WHERE task_id=$1 AND worker='worker' LIMIT 1",(str(task_id),))):raise RuntimeError('Worker effects require separate reconciliation')
            if tx.fetch_all(Query("SELECT 1 FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' LIMIT 1",(exploration_id,))):raise RuntimeError('Intent effects require separate reconciliation')
            report=json.dumps({'summary':'Integration attempt ended before inventory execution; no inventory operations were replayed.','reason':'reconciled_before_inventory'})
            tx.execute(Query("UPDATE tasks SET status='failed',paused=false,queued=false,completed_at=COALESCE(completed_at,now()) WHERE id=$1 AND status='running'",(task_id,)))
            tx.execute(Query("UPDATE artex_assessments SET state='failed',report=$2::jsonb WHERE id=$1",(identifier,report)))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-id',type=int,required=True)
    parser.add_argument('--api-key-env',required=True)
    parser.add_argument('--allow-origin',action='append',required=True)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--extensions-file',type=Path)
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--install-schema',action='store_true')
    parser.add_argument('--migrate-legacy',action='store_true')
    parser.add_argument('--reconcile-no-observation-task',type=int,action='append',default=[])
    parser.add_argument('--denied-password-file',type=Path)
    parser.add_argument('--denied-username',default='admin')
    args=parser.parse_args(argv)
    dsn,key=_configuration(os.environ)
    root=args.state_root
    if not root.is_absolute() or root.resolve()!=root:raise ValueError('Absolute canonical state root required')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    credentials=CredentialStore(root/'credentials',key)
    transcripts=root/'transcripts';transcripts.mkdir(mode=0o700,exist_ok=True)
    extensions=ExtensionHost(root/'artifacts',args.allow_origin,args.extensions_file)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    import psycopg
    with psycopg.connect(dsn,autocommit=True) as owner:
        if not owner.execute('SELECT pg_try_advisory_lock(%s)',(LOCK_KEY,)).fetchone()[0]:raise RuntimeError('Database already owned')
        if args.install_schema:install(owner,extensions.catalog)
        pool=ExecutorPool.postgres(dsn,max_size=12)
        if args.migrate_legacy:migrate_legacy(pool)
        reconcile_before_inventory(pool,args.reconcile_no_observation_task)
        if args.denied_password_file:
            if len(args.allow_origin)!=1:raise ValueError('Exact denied credential origin required')
            origin=AssessmentStore(pool,args.allow_origin).approved(args.allow_origin[0])
            ref=credentials.put(origin,args.denied_username,private_file(args.denied_password_file))
            pool.execute(Query('INSERT INTO artex_assessment_denials(credential_ref) VALUES($1) ON CONFLICT DO NOTHING',(ref,)))
        profile=ProfileStore(pool).get(args.profile_id)
        if profile is None:raise ValueError('Stored provider profile required')
        digest=hashlib.sha256()
        for path in sorted(Path(__file__).parent.rglob('*.py')):digest.update(str(path.relative_to(Path(__file__).parent)).encode()+b'\0'+path.read_bytes())
        runtime=AssessmentRuntime(pool,profile,os.environ[args.api_key_env],credentials,transcripts,args.allow_origin,key,BcryptCodec(),revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=digest.hexdigest(),extensions=extensions)
        app=register(runtime,runtime.bundle.app)
        config=DevListenerConfig(args.port,8,30,'http://127.0.0.1:3000');config.validate()
        server=make_server('127.0.0.1',args.port,app,server_class=_BoundedServer,handler_class=QuietRuntimeHandler)
        server.configure(config,app.max_body_bytes)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=False)
        runtime.start();thread.start()
        print(json.dumps({'event':'ready','url':f'http://127.0.0.1:{args.port}','model':profile.model,'runtime':'shared-task-engine'}),flush=True)
        stop.wait();runtime.bundle.admission.stop_admissions();server.shutdown();thread.join(5)
        if not server.quiesce(30):raise RuntimeError('HTTP requests remain')
        server.server_close();drain=runtime.close()
        if not pool.close(timeout=5).drained:raise RuntimeError('Database work remains')
        print(json.dumps({'event':'closed','drain':drain}),flush=True)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('Assessment launcher failed: '+type(exc).__name__,flush=True)
        raise SystemExit(1) from None
