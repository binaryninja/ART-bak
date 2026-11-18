#!/usr/bin/env python3
"""Exercise configurable assistants against a real provider in a disposable DB."""
import argparse,json,os,secrets,tempfile,time,uuid
from pathlib import Path
import psycopg
from psycopg import sql
from artex.db.pool import ExecutorPool
from artex.db.profiles import ProfileStore
from artex.db.schema import SchemaExecutor,bootstrap_empty_database
from artex.db.recording_schema import ensure_recording_schema
from artex.assessment_server import install
from artex.assessments.credentials import CredentialStore
from artex.assessments.runtime import AssessmentRuntime
from artex.assessments.extensions import ExtensionHost
from artex.server.passwords import BcryptCodec
from artex.server.core import Request
from artex.server.auth import sign_jwt
from artex.db.core import Query

def require(value,message):
    if not value:raise AssertionError(message)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-profile-id',type=int,required=True);parser.add_argument('--receipt',type=Path,required=True);args=parser.parse_args()
    source=os.environ['ARTEX_ACCEPTANCE_SOURCE_DSN'];database='artex_assist_'+uuid.uuid4().hex[:20]
    dsn=psycopg.conninfo.make_conninfo(source,dbname=database)
    root=Path(tempfile.mkdtemp(prefix='artex-assistant-'));key=secrets.token_bytes(32);pool=None;runtime=None
    source_pool=ExecutorPool.postgres(source,max_size=1)
    profile=ProfileStore(source_pool).get(args.source_profile_id);source_pool.close(timeout=5)
    require(profile is not None,'Source profile missing');profile.id=0;profile.api_key='';profile.api_key_hint=''
    with psycopg.connect(source,autocommit=True) as admin:admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        extensions=ExtensionHost(root/'artifacts',('https://dev366312.service-now.com',))
        with psycopg.connect(dsn,autocommit=True) as connection:
            adapter=SchemaExecutor(connection,cursor_factory=psycopg.RawCursor)
            bootstrap_empty_database(adapter,expected_database=database,allow_schema_creation=True)
            ensure_recording_schema(adapter,expected_database=database,action='create_or_upgrade')
            install(connection,extensions.catalog)
        pool=ExecutorPool.postgres(dsn,max_size=6)
        profile=ProfileStore(pool).get(ProfileStore(pool).save(profile))
        (root/'transcripts').mkdir(mode=0o700)
        runtime=AssessmentRuntime(pool,profile,os.environ['LITELLM_API_KEY'],CredentialStore(root/'credentials',key),root/'transcripts',('https://dev366312.service-now.com',),key,BcryptCodec(),revision='acceptance',extensions=extensions)
        router=runtime.bundle.app.router;headers={'Authorization':'Bearer '+sign_jwt(key)}
        def call(method,path,body=None,client=None):
            h=dict(headers)
            if client:h['Idempotency-Key']=client
            result=router.dispatch(Request(method,'/api'+path,h,b'' if body is None else json.dumps(body).encode()))
            require(result.status<300,'HTTP '+str(result.status)+' '+path+' '+result.body.decode()[:160]);return result.json()
        def wait_conversation(identifier):
            deadline=time.monotonic()+180
            while runtime.assistants.dispatcher.running(identifier) and time.monotonic()<deadline:time.sleep(.1)
            require(not runtime.assistants.dispatcher.running(identifier),'Conversation did not settle')
            with psycopg.connect(dsn) as fresh:
                rows=fresh.execute("SELECT detail,is_error FROM conversation_activities WHERE conversation_id=%s AND kind='result' ORDER BY id DESC",(identifier,)).fetchall()
            require(rows and not rows[0][1],'Durable conversation result missing');return rows[0][0]
        require(any(a['key']=='servicenow_analyst' for a in call('GET','/agents')['agents']),'Specialist missing')
        call('POST','/agents',{'key':'acceptance_reader','name':'Acceptance reader'})
        call('PUT','/agents/acceptance_reader/config',{'llm_profile_id':profile.id,'max_turns':4,'run_seconds':180})
        call('PUT','/agents/acceptance_reader/prompt',{'template':'Call list_assessments exactly once. Report the registered definition keys.','note':'Acceptance'})
        call('PUT','/agents/acceptance_reader/visibility',{'mcp':[],'skill':[],'tools':['list_assessments']})
        identifier=int(call('POST','/conversations',{'agent_key':'acceptance_reader','title':'Provider acceptance'})['id'])
        call('POST',f'/conversations/{identifier}/messages',{'message':'List the registered assessments.'},'acceptance-once')
        result=wait_conversation(identifier)
        require('servicenow.public_rest' in result,'Provider did not report tool result')
        transcript=list((root/'transcripts').rglob('*.jsonl'))
        require(any('list_assessments' in p.read_text() and 'tool_use' in p.read_text() for p in transcript),'Durable tool exchange missing')
        trigger=call('POST','/agents/acceptance_reader/triggers',{'enabled':True,'on_task_create':True,'task_create_message':'List the registered assessments.'})
        pool.execute(Query("INSERT INTO artex_agent_events(event_key,kind,payload) VALUES($1,'task_create','{}'::jsonb)",('acceptance:'+uuid.uuid4().hex,)))
        runtime.agent_events.tick()
        delivery=pool.fetch_one(Query('SELECT conversation_id,state FROM artex_agent_deliveries WHERE trigger_id=$1',(trigger['id'],)))
        require(delivery and delivery[1]=='running','Trigger did not admit');wait_conversation(delivery[0]);runtime.agent_events.tick()
        require(pool.fetch_one(Query('SELECT state FROM artex_agent_deliveries WHERE trigger_id=$1',(trigger['id'],)))[0]=='done','Trigger not settled')
        drain=runtime.close();runtime=None
        receipt=dict(database=database,model=profile.model,conversation_id=identifier,trigger_id=trigger['id'],trigger_conversation_id=delivery[0],transcripts=str(root/'transcripts'),drain=drain,verified=True)
        args.receipt.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
    finally:
        if runtime is not None:runtime.close()
        if pool is not None:pool.close(timeout=5)
        with psycopg.connect(source,autocommit=True) as admin:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))

if __name__=='__main__':main()
