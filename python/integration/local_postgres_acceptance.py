#!/usr/bin/env python3
"""Opt-in synthetic acceptance; never use against real or shared data."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile


def validate_arguments(args):
    if not __debug__:
        raise ValueError("Assertions must be enabled; do not use -O or PYTHONOPTIMIZE")
    if args.confirm_disposable != args.database or not re.fullmatch(r'artex_test_[a-z0-9_]{1,40}',args.database):
        raise ValueError('Exact disposable database confirmation and artex_test_ name required')
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}',args.user) or not 1<=args.port<=65535:
        raise ValueError('Explicit local user and port required')
    if any(name.startswith('PG') for name in os.environ):
        raise ValueError('Remove ambient PG connection settings before running')
    if args.host.startswith('/'):
        root=Path(args.host)
        for component in (root,*root.parents):
            if component.is_symlink():raise ValueError('Socket path must not contain symlinks')
        temp=Path(tempfile.gettempdir()).resolve()
        resolved=root.resolve(strict=True)
        if resolved==temp or temp not in resolved.parents:raise ValueError('Socket directory must be under the temporary directory')
        info=root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('Socket directory must be owner-only0700 and owned by current user')
    elif args.host not in ('127.0.0.1','::1'):
        raise ValueError('Only literal loopback or owner-only temporary socket directory allowed')


def verify_connection(connection,args):
    row=connection.execute('SELECT current_database(),current_user,host(inet_server_addr())').fetchone()
    if row[:2]!=(args.database,args.user):raise ValueError('Connected identity differs from explicit target')
    if args.host.startswith('/'):
        if row[2] is not None:raise ValueError('Expected local Unix socket')
    elif row[2] is None or not ipaddress.ip_address(row[2]).is_loopback:
        raise ValueError('Expected actual loopback server')


def run(args,workspace,connection):
    signing_key=secrets.token_bytes(32)
    fixture_password=secrets.token_urlsafe(24)
    import os,json,traceback,threading,psycopg,pathlib
    from contextlib import contextmanager
    from dataclasses import replace
    from artex.db.schema import SchemaExecutor,bootstrap_empty_database
    from artex.db.pool import ExecutorPool
    from artex.db.core import Query
    from artex.db.tasks import TaskStore
    from artex.db.exploration import ExplorationStore
    from artex.db.workers import WorkerStore,WorkerConflict
    from artex.agent.planner import PlannerCycle,PlannerScope
    from artex.agent.worker import IntentWorker,WorkerScope,WorkerUnresolved
    from artex.agent.chat import ChatBinding
    from artex.agent.runtime import Grant,Tool
    from artex.sidequestion import Model
    from artex.tools.registry import ToolRegistry
    from artex.db.tool_catalog import CatalogTool
    from artex.transcripts import TranscriptStore,TranscriptConflict
    from artex.chat_transcripts import ChatTranscriptAdapter
    errors=[]
    with psycopg.connect(**connection,autocommit=True) as c:
     verify_connection(c,args)
     bootstrap_empty_database(SchemaExecutor(c,cursor_factory=psycopg.RawCursor),expected_database=args.database,allow_schema_creation=True)
    def check(name,fn):
     try:fn();print('PASS',name,flush=True)
     except Exception as x:errors.append(name);print('FAIL',name,type(x).__name__,flush=True);print('Fixture failed; database retained for inspection',flush=True)
    pool=ExecutorPool.postgres(psycopg.conninfo.make_conninfo(**connection),max_size=6);lock=threading.RLock()
    @contextmanager
    def guard(*a):
     with lock:yield
    ALLOW=lambda *a:Grant(True,True,True);model=Model(model='dummy',identity='fixture',streaming=False)
    class Provider:
     def __init__(self,responses):self.responses=responses;self.calls=[];self.hook=None
     def complete(self,ctx,req):
      self.calls.append(req)
      if self.hook:self.hook()
      return self.responses.pop(0)
    def text(s):return ({'role':'assistant','content':[{'type':'text','text':s}]},'end_turn',{})
    def call(name,args):return ({'role':'assistant','content':[{'type':'tool_use','id':'fixture','name':name,'input':args}]},'tool_use',{})

    from artex.agent.engine import TaskEngineHandle,EngineScope
    from artex.tools.graph import GraphTools,GraphScope
    from artex.agent.bootstrap import BootstrapCoordinator,BootstrapJournal,install_journal
    from artex.agent.bootstrap_handle import BootstrapHandle
    from artex.agent.goals import GoalPlanner
    from artex.agent.engine_runner import EngineRunner
    from artex.server.hosted_setup import build_hosted_application
    from artex.server.passwords import BcryptCodec
    import io
    with psycopg.connect(**connection,autocommit=True) as c:install_journal(SchemaExecutor(c,cursor_factory=psycopg.RawCursor),expected_database=args.database,expected_schema='public',allow_create=True)
    root=pathlib.Path(workspace)/'transcripts';root.mkdir();number=[0];jwt=None
    def req(bundle,path,data=None,auth=True):
     raw=json.dumps(data or {}).encode();status=[]
     env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
     if auth and jwt:env['HTTP_AUTHORIZATION']='Bearer '+jwt
     body=b''.join(bundle.app(env,lambda s,h:status.append(s)))
     return int(status[0].split()[0]),json.loads(body)

    from artex.agent.bootstrap_setup import build_bootstrap_application,BootstrapConfiguration
    def hosted(fail_json=False):
     built=[];boot=[];resources={};configs=[]
     def planner_factory(scope,current,lifecycle_guard):
      number[0]+=1;folder=root/str(number[0]);folder.mkdir();tx=ChatTranscriptAdapter(TranscriptStore(str(folder)))
      graph=ExplorationStore(pool,scope.exploration_id);provider=Provider([call('set_goals',{'text':'authorized fixture goal'}),text('prepared')]);boot.append(provider);resources[scope.task_id]=(graph,tx)
      if fail_json:tx.commit=lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic journal uncertainty'))
      return GoalPlanner(store=graph,transcript=tx,current=current,lifecycle_guard=lifecycle_guard,provider_resolver=lambda s,m:ChatBinding(m,provider),authorize=ALLOW,validate_input=lambda *a:True)
     coord=BootstrapCoordinator(BootstrapJournal(pool),planner_factory=planner_factory,lifecycle_guard=guard,current=lambda t,e,r:True)
     def engine_factory(row,token,scope,current_cb,guard_cb):
      task=TaskStore(pool).get(scope.task_id);graph,tx=resources[scope.task_id]

      goal=pool.fetch_one(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='goal'",(task.exploration_id,)))[0]

      current=lambda:current_cb(scope,token)
      pscope=PlannerScope(task.id,task.exploration_id,scope.run_id,'fixture',model,'Inert planner')
      proof=GraphTools(graph,GraphScope(task.id,task.exploration_id,scope.run_id,'fixture','planner'),current=lambda s:current(),lifecycle_guard=guard,authorize=ALLOW,asset_authorize=ALLOW)
      proof_tool=next(t for t in proof.tools() if t.name=='prove_goal')
      class PP:
       calls=[]
       def __init__(self):self.calls=[]
       def complete(self,context,request):
        self.calls.append(request);n=len(self.calls)
        if n==1:return call('add_intent',{'summary':'record inert fact'})
        if n==2:return text('intent queued')
        if n==3:
         fact=pool.fetch_one(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='fact' AND state='confirmed' ORDER BY id DESC LIMIT 1",(task.exploration_id,)))[0]
         result=call('prove_goal',{'goal_id':goal,'evidence_id':fact,'reason':'inert authorized proof fixture'});result[0]['content'][0]['id']='fixture-proof';return result
        return text('goals reviewed')
      pp=PP();wp=Provider([call('record_fact',{'summary':'inert observation','evidence':'fixture only'}),text('worker done')])
      planner=PlannerCycle(store=graph,transcript=tx,current=lambda s:current(),lifecycle_guard=guard,provider_resolver=lambda s,m:ChatBinding(m,pp),authorize=ALLOW,asset_authorize=ALLOW,additional_tools=[proof_tool],additional_catalog=[CatalogTool(proof_tool.name,True,proof_tool.description,proof_tool.input_schema,['planner'],True,'builtin',{},False)])
      def registry(s,record):
       graph_tools=GraphTools(graph,GraphScope(task.id,task.exploration_id,scope.run_id,'fixture','worker',s.intent_id),current=lambda _:current(),lifecycle_guard=guard,authorize=ALLOW,asset_authorize=ALLOW)
       tool=next(t for t in graph_tools.tools() if t.name=='record_fact');r=ToolRegistry('worker',handlers=[tool],current=lambda _:current(),lifecycle_guard=guard,authorize=ALLOW);r.reload([CatalogTool(tool.name,True,tool.description,tool.input_schema,['worker'],True,'builtin',{},False)],'fixture');return r
      worker=IntentWorker(store=WorkerStore(pool,task.id,task.exploration_id,lifetime_guard=guard),transcript=tx,current=lambda s,phase:current(),lifecycle_guard=guard,provider_resolver=lambda s,m:ChatBinding(m,wp),registry_factory=registry,authorize=ALLOW)
      handle=TaskEngineHandle(scope,token,store=graph,planner=planner,planner_scope=pscope,worker=worker,worker_model=model,worker_system='Inert worker',owner='fixture-worker',current=current_cb,lifecycle_guard=guard_cb,single_process=True)
      original_run=planner.run
      def observed_run(wake):
       try:return original_run(wake)
       except BaseException:
        print('Fixture failed; database retained for inspection',flush=True);raise
      planner.run=observed_run
      built.append((handle,pp,wp));return handle
     def config(row):
      configs.append(row);return BootstrapConfiguration(model,'Explicit goals only fixture','fixture','fixture-bootstrap')
     bundle=build_bootstrap_application(pool,signing_key,BcryptCodec(),coordinator=coord,bootstrap_configuration=config,engine_factory=engine_factory,initialized_readiness=lambda row:None,initialized_factory=lambda *a:None,live_last_activity=lambda id:0,capacity=1,max_pending=4,single_process=True)
     assert not configs and not boot and not built
     return bundle,bundle.runner,built,boot,bundle.admission
    def create(bundle):
     code,result=req(bundle,'/api/tasks',{'description':'inert initialization fixture','goal':'inert proof goal'})
     assert code in (200,201),(code,result)
     return TaskStore(pool).get(pool.fetch_one(Query('SELECT max(id) FROM tasks'))[0])
    def step(bundle,r,h,n=0):return bundle.host.advance_owned(lambda:r.advance(h.task_id,h.token,n))
    def connected():
     nonlocal jwt
     b,r,built,boot,cap=hosted();code,data=req(b,'/api/auth/init',{'password':fixture_password},False);assert code==200; jwt=data['token']
     t=create(b);h=r.handles()[0];token=h.token
     assert not boot and not built and h.scope is None
     assert pool.fetch_one(Query('SELECT count(*) FROM artex_task_bootstrap'))[0]==0
     assert b.manager.owns_generation(h.task_id,token,h)
     result=step(b,r,h);assert h.phase=='engine',result
     assert TaskStore(pool).get(t.id).status=='running' and h.scope.run_id>0
     assert len(boot[0].calls)==2 and not built[0][1].calls and b.host.poll().settled==()
     assert r.handles()[0] is h and h.token is token and b.manager.owns_generation(h.task_id,token,h)
     for n in range(1,5):step(b,r,h,n)
     assert b.host.poll().settled==(str(t.id),)
     assert TaskStore(pool).get(t.id).status=='done' and not r.handles()
     assert len(built[0][1].calls)==4 and len(built[0][2].calls)==2
    check('JWT HTTP create installed bootstrap prepared same ownership then actual proof manager completion',connected)

    def read_acceptance():
     from artex.db.token_usage import TokenUsageStore,UsageScope
     from artex.db.command_history import CommandHistoryStore,CommandScope
     task=TaskStore(pool).get(pool.fetch_one(Query('SELECT max(id) FROM tasks'))[0])
     pool.execute(Query("INSERT INTO activity(exploration_id,worker,kind,input_tokens,output_tokens) VALUES($1,'fixture','result',17,3)",(task.exploration_id,)))
     usage=TokenUsageStore(pool,UsageScope('isolated-fixture',task.id,task.exploration_id),authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=guard)
     assert usage.exploration()['total']['input_tokens']>=17
     for kind,detail in [('tool_use','opaque synthetic input'),('tool_result','opaque synthetic output')]:
      pool.execute(Query("INSERT INTO activity(exploration_id,worker,kind,tool,tool_use_id,detail) VALUES($1,'fixture',$2,'InertFixture','read-fixture',$3)",(task.exploration_id,kind,detail)))
     commands=CommandHistoryStore(pool,CommandScope('isolated-fixture',task.id,task.exploration_id),authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=guard,raw_authorize=lambda *a:True)
     rows,total=commands.list('opaque synthetic input')
     assert total==1 and rows[0]['output']=='opaque synthetic output'
    check('actual scoped token and command read projections',read_acceptance)
    def diagnostic_acceptance():
     from artex.db.intercept_history import InterceptHistoryStore,InspectionScope
     from artex.server.intercept_inspection import InterceptInspectionService
     from artex.server.core import Request,Router
     from artex.health import Registry,State
     from artex.db.settings import SettingsStore
     from artex.server.pool_health import PoolHealthService
     from artex.guard import Guard
     from artex.server.audit import AuditService,AuditBinding
     # Exact TEXT keys deliberately differ only in leading zero. No task FK inference.
     conv=pool.fetch_one(Query("INSERT INTO conversations(agent_key,title) VALUES('fixture','Inspection fixture') RETURNING id"))[0]
     ids=[]
     for key in ('007','7'):
      ids.append(pool.fetch_one(Query("INSERT INTO intercept_pending(task_id,conversation_id,tool_name,tool_input) VALUES($1,$2,'InertFixture',$3::jsonb) RETURNING id",(key,conv,'{"marker":"synthetic-private"}')))[0])
     raw=[True]
     def inspection(executor):
      store=InterceptHistoryStore(executor,InspectionScope('isolated-fixture','007'),authorize=lambda *a:True,raw_authorize=lambda *a:raw[0],current=lambda *a:True,lifecycle_guard=guard)
      service=InterceptInspectionService(store,authorize=lambda *a:True)
      router=Router(lambda request:request.header('Authorization')=='fixture-read')
      service.register(router)
      return router
     request=Request('GET','/api/intercept/history?page=1&size=1',{'Authorization':'fixture-read'})
     response=inspection(pool).dispatch(request)
     assert response.status==200
     payload=response.json()
     assert payload['total']==1 and payload['items'][0]['id']==ids[0]
     assert payload['items'][0]['conv_title']=='Inspection fixture'
     # Delegate real SQL, revoke the raw grant after fetch, before DTO/HTTP return.
     class RevokeAfterRead:
      def fetch_all(self,query):
       rows=pool.fetch_all(query)
       raw[0]=False
       return rows
     response=inspection(RevokeAfterRead()).dispatch(request)
     assert response.status==403 and b'synthetic-private' not in response.body
     profile=pool.fetch_one(Query("INSERT INTO llm_profiles(name,format,model) VALUES('Inspection fixture','openai','inert') RETURNING id"))[0]
     registry=Registry(clock=lambda:100)
     registry.restore(profile,State(0,1,100.2,'synthetic-private',99.9))
     health=PoolHealthService(SettingsStore(pool),registry,scope='isolated-fixture',authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=guard,clock=lambda:100)
     response=health.handle('status',Request('GET','/api/llm/pool'))
     assert response.status==200 and b'synthetic-private' not in response.body
     row=next(row for row in response.json()['chain'] if row['profile_id']==str(profile))
     assert row['cooldown_secs']==1 and row['last_at']=='1970-01-01T00:01:39Z'
     audit=Guard()
     for number in range(2001):audit._record('Read','allow',reason=str(number))
     audit_service=AuditService(resolve=lambda selector:AuditBinding(1,'fixture',audit),authorize_selection=lambda *a:True,raw_authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=guard)
     response=audit_service.handle(Request('GET','/api/audit?task=1'))
     assert response.status==200
     entries=response.json()['entries']
     assert len(entries)==2000 and entries[0]['reason']=='1' and entries[-1]['reason']=='2000'
    check('actual intercept scoped reads and revocation plus pool and audit diagnostics',diagnostic_acceptance)
    assert pool.close(timeout=3).drained
    if errors:raise RuntimeError('Acceptance fixture failed')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--port',type=int,default=5432)
    parser.add_argument('--database',required=True)
    parser.add_argument('--user',required=True)
    parser.add_argument('--confirm-disposable',required=True,metavar='EXACT_DATABASE_NAME')
    args=parser.parse_args()
    validate_arguments(args)
    with tempfile.TemporaryDirectory(prefix='artex-acceptance-') as workspace:
        passfile=Path(workspace)/'empty.pgpass'
        passfile.touch(mode=0o600)
        connection=dict(host=args.host,port=args.port,dbname=args.database,user=args.user,
                        password='',passfile=str(passfile),sslmode='disable',connect_timeout=3,
                        options='-c statement_timeout=4000',application_name='artex_disposable_acceptance')
        run(args,workspace,connection)
    print('PASS: synthetic acceptance complete; disposable database remains populated and reruns refuse it')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('REFUSED/FAILED:',type(error).__name__,'No automatic cleanup or retry. Inspect the disposable database.')
        raise SystemExit(1)
