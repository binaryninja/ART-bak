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
from .recon.servicenow import EnumerationPolicy
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
        from .server.assistant_host import install as install_assistants
        install_assistants(connection)
        from .server.agent_events import DDL as AGENT_EVENTS_DDL
        connection.execute(AGENT_EVENTS_DDL)
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
            row=tx.fetch_one(Query("SELECT a.id,t.exploration_id,t.status FROM artex_assessments a JOIN tasks t ON t.id=a.task_id WHERE t.id=$1 AND a.version IN (1,2,3) FOR UPDATE OF a,t",(task_id,)))
            if row is None or row[2]!='running':raise RuntimeError('Recovery task is not an abandoned assessment')
            identifier,exploration_id,_=row
            if tx.fetch_all(Query('SELECT 1 FROM artex_assessment_observations WHERE assessment_id=$1 LIMIT 1',(identifier,))):raise RuntimeError('Inventory effects require separate reconciliation')
            if tx.fetch_all(Query("SELECT 1 FROM llm_records WHERE task_id=$1 AND worker='worker' LIMIT 1",(str(task_id),))):raise RuntimeError('Worker effects require separate reconciliation')
            if tx.fetch_all(Query("SELECT 1 FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' LIMIT 1",(exploration_id,))):raise RuntimeError('Intent effects require separate reconciliation')
            report=json.dumps({'summary':'Integration attempt ended before inventory execution; no inventory operations were replayed.','reason':'reconciled_before_inventory'})
            tx.execute(Query("UPDATE tasks SET status='failed',paused=false,queued=false,completed_at=COALESCE(completed_at,now()) WHERE id=$1 AND status='running'",(task_id,)))
            tx.execute(Query("UPDATE artex_assessments SET state='failed',report=$2::jsonb WHERE id=$1",(identifier,report)))

def reconcile_reported(pool,task_ids):
    """Fail an abandoned reported run without replaying or claiming settlement."""
    from .assessments.definitions import DEFINITIONS
    for task_id in task_ids:
        with pool.transaction() as tx:
            row=tx.fetch_one(Query("SELECT a.id,a.definition,a.version,a.report,t.exploration_id,t.status,b.run_id FROM artex_assessments a JOIN tasks t ON t.id=a.task_id JOIN artex_task_bootstrap b ON b.task_id=t.id WHERE t.id=$1 FOR UPDATE OF a,t",(task_id,)))
            if row is None:raise RuntimeError('Recovery assessment not found')
            identifier,definition,version,report,exploration_id,status,run_id=row
            if status not in ('running','paused') or definition not in DEFINITIONS or version!=DEFINITIONS[definition].version or not isinstance(report,dict) or report.get('run_id')!=run_id:raise RuntimeError('Recovery requires an abandoned reported generation')
            operations={r[0] for r in tx.fetch_all(Query('SELECT operation FROM artex_assessment_observations WHERE assessment_id=$1 AND run_id=$2',(identifier,run_id)))}
            if operations!=set(DEFINITIONS[definition].operations):raise RuntimeError('Incomplete reported inventory')
            calls=tx.fetch_all(Query('SELECT tool,status,run_id FROM artex_tool_calls WHERE task_id=$1',(task_id,)))
            if not calls or any(state!='completed' or generation!=run_id for _,state,generation in calls) or not {'run_assessment','finish_assessment'}<={name for name,_,_ in calls}:raise RuntimeError('Unconfirmed tool effects require separate reconciliation')
            goals=tx.fetch_all(Query("SELECT state FROM exploration_nodes WHERE exploration_id=$1 AND kind='goal'",(exploration_id,)))
            if not goals or any(state!='met' for state, in goals):raise RuntimeError('Reported goals are not met')
            tx.execute(Query("UPDATE exploration_nodes SET state='blocked',blocked_reason='Abandoned generation; transcript settlement unconfirmed',content_version=content_version+1 WHERE exploration_id=$1 AND kind='intent' AND state IN ('open','running')",(exploration_id,)))
            tx.execute(Query("UPDATE tasks SET status='failed',paused=false,queued=false,completed_at=COALESCE(completed_at,now()) WHERE id=$1",(task_id,)))
            tx.execute(Query("UPDATE artex_assessments SET state='failed',report=report || $2::jsonb WHERE id=$1",(identifier,json.dumps({'reconciliation':{'reason':'reported_generation_abandoned','settlement_confirmed':False,'run_id':run_id}}))))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-id',type=int,required=True)
    parser.add_argument('--api-key-env',required=True)
    parser.add_argument('--allow-origin',action='append',required=True)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--extensions-file',type=Path)
    parser.add_argument('--inventory-page-size',type=int,default=250)
    parser.add_argument('--inventory-max-records',type=int,default=10000)
    parser.add_argument('--inventory-large-max-records',type=int,default=50000)
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--install-schema',action='store_true')
    parser.add_argument('--migrate-legacy',action='store_true')
    parser.add_argument('--reconcile-no-observation-task',type=int,action='append',default=[])
    parser.add_argument('--reconcile-reported-task',type=int,action='append',default=[])
    parser.add_argument('--denied-password-file',type=Path)
    parser.add_argument('--denied-username',default='admin')
    args=parser.parse_args(argv)
    enumeration=EnumerationPolicy(args.inventory_page_size,args.inventory_max_records,args.inventory_large_max_records)
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
        reconcile_reported(pool,args.reconcile_reported_task)
        if args.denied_password_file:
            if len(args.allow_origin)!=1:raise ValueError('Exact denied credential origin required')
            origin=AssessmentStore(pool,args.allow_origin).approved(args.allow_origin[0])
            ref=credentials.put(origin,args.denied_username,private_file(args.denied_password_file))
            pool.execute(Query('INSERT INTO artex_assessment_denials(credential_ref) VALUES($1) ON CONFLICT DO NOTHING',(ref,)))
        profile=ProfileStore(pool).get(args.profile_id)
        if profile is None:raise ValueError('Stored provider profile required')
        digest=hashlib.sha256()
        for path in sorted(Path(__file__).parent.rglob('*.py')):digest.update(str(path.relative_to(Path(__file__).parent)).encode()+b'\0'+path.read_bytes())
        for path in (Path(__file__).parent/'assessments'/'servicenow_reviewed_probes.json',Path(__file__).resolve().parents[2]/'skills'/'servicenow-public-rest'/'SKILL.md'):
            digest.update(path.name.encode()+b'\0'+path.read_bytes())
        runtime=AssessmentRuntime(pool,profile,os.environ[args.api_key_env],credentials,transcripts,args.allow_origin,key,BcryptCodec(),revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=digest.hexdigest(),extensions=extensions,enumeration=enumeration)
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
