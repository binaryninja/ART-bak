"""Configurable assistants composed with the shared durable conversation engine."""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib,hmac,json,os,re,threading
from pathlib import Path
from ..agent.chat import ChatBinding,ChatUnavailable
from ..agent.runtime import Grant,Limits,Tool
from ..chat_dispatch import ChatDispatcher
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore
from ..db.agents import AgentStore
from ..db.conversations import ConversationStore
from ..db.conversation_activity import ConversationActivityStore
from ..db.core import Query
from ..db.profiles import ProfileStore
from ..db.tool_catalog import ToolCatalogStore
from ..sidequestion import Model
from .chat import ChatResources,ChatService
from .core import json_response
from .agents import AgentService
from .agent_management import AgentManagement
from ..assessments.definitions import DEFINITIONS

SPECIALIST='servicenow_analyst'
SKILL='servicenow-public-rest'
SKILL_PATH=Path(__file__).resolve().parents[3]/'skills'/SKILL/'SKILL.md'
DEFAULT='You are a scoped assessment assistant. Use registered tools to inspect existing evidence and submit versioned assessments. Never claim completion without reading the persisted result.'
SPECIALIST_PROMPT=DEFAULT+' For ServiceNow public REST analysis, load the servicenow-public-rest skill. Reuse inventory credentials through the host; never request or repeat stored secrets. Submit servicenow.public_rest when asked to assess public endpoints. Report the returned task link and distinguish pending work from verified findings.'

def schema(properties,required=()):return dict(type='object',properties=properties,required=list(required),additionalProperties=False)
SCHEMAS={'list_assessments':schema({}),
 'submit_assessment':schema({'definition':{'type':'string','enum':list(DEFINITIONS)},'url':{'type':'string'},'request_id':{'type':'string'}},('definition','url')),
 'read_assessment':schema({'task_id':{'type':'integer'}},('task_id',)),
 'Skill':schema({'name':{'type':'string'}},('name',))}
DESCRIPTIONS={'list_assessments':'List registered definitions and admitted assessment tasks.',
 'submit_assessment':'Submit or reuse a scoped versioned assessment; return its durable task identity.',
 'read_assessment':'Read the persisted report and status of an assessment task.',
 'Skill':'Load a visible registered assessment methodology.'}

def install(connection):
    connection.execute("INSERT INTO agents(key,name,description,role,builtin,enabled) VALUES(%s,'ServiceNow analyst','Reusable ServiceNow assessment coordinator','assistant',false,true) ON CONFLICT(key) DO NOTHING",(SPECIALIST,))
    identifier=connection.execute('SELECT id,current_prompt_id FROM agents WHERE key=%s',(SPECIALIST,)).fetchone()
    if identifier[1] is None:
        prompt=connection.execute("INSERT INTO agent_prompts(agent_id,version,template_text,note,updated_by) SELECT %s,COALESCE(MAX(version),0)+1,%s,'Built-in default','system' FROM agent_prompts WHERE agent_id=%s RETURNING id",(identifier[0],SPECIALIST_PROMPT,identifier[0])).fetchone()[0]
        connection.execute('UPDATE agents SET current_prompt_id=%s WHERE id=%s',(prompt,identifier[0]))
    for name,definition in SCHEMAS.items():
        connection.execute("INSERT INTO tools(key,system,description,schema,agents,enabled,kind,exec,deferred) VALUES(%s,true,%s,%s::jsonb,%s::jsonb,true,'builtin','{}'::jsonb,false) ON CONFLICT(key) DO NOTHING",(name,DESCRIPTIONS[name],json.dumps(definition),json.dumps([SPECIALIST])))
    connection.execute('INSERT INTO agent_skill_visibility(agent_id,skill_name,enabled) VALUES(%s,%s,true) ON CONFLICT DO NOTHING',(identifier[0],SKILL))

class AssistantHost:
    def __init__(self,runtime,factory,transcript_root):
        self.runtime,self.pool,self.factory=runtime,runtime.pool,factory
        self.store=AgentStore(self.pool);self.conversations=ConversationStore(self.pool)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(str(transcript_root)))
        self.salt=os.urandom(32);self.lock=threading.RLock()
        self.dispatcher=ChatDispatcher(workers=4,capacity=16,max_receipts=10000,single_process=True)
        self.service=ChatService(self.conversations,self.resolve,dispatcher=self.dispatcher,
            activities=lambda identifier:ConversationActivityStore(self.pool,identifier),authorize_conversation=lambda request,conversation:self.ready(conversation.agent_key))
        self.management=AgentManagement(self.store,profile_available=self.profile_available,tool_names=SCHEMAS,skills=(SKILL,),render=self.render,defaults=lambda key:SPECIALIST_PROMPT if key==SPECIALIST else DEFAULT,lock=self.lock)
        self.catalog=AgentService(self.store,readiness=lambda agent:self.ready(agent.key),management=self.management,
            detail_defaults=lambda agent:dict(wrapup_default='',wrapup_max_turns_default=10,task_timeout_wrapup_default='',task_timeout_wrapup_max_turns_default=10))
    def profile_available(self,identifier):return identifier==self.runtime.profile.id and ProfileStore(self.pool).get(identifier)==self.runtime.profile
    def ready(self,key):
        row=self.store.get(key)
        return bool(row and row.enabled and row.role=='assistant' and key!='retester' and not row.web_search and not row.interactive_shell and (row.llm_profile_id is None or self.profile_available(row.llm_profile_id)))
    def render(self,text,sample):
        values={'Now':datetime.now(timezone.utc).isoformat(),'DataDir':str(self.runtime.extensions.root)}
        if not isinstance(sample,dict) or set(sample)-values.keys():raise ValueError('Unknown prompt variable')
        values.update(sample)
        def replace(match):
            if match[1] not in values:raise ValueError('Unknown prompt variable')
            return str(values[match[1]])
        result=re.sub(r'\{\{\s*\.?([A-Za-z][A-Za-z0-9_]*)\s*\}\}',replace,text)
        if '{{' in result or '}}' in result:raise ValueError('Unsupported prompt expression')
        return result
    def snapshot(self,key):
        agent=self.store.get(key)
        if not self.ready(key):raise ChatUnavailable('Agent unavailable')
        detail=self.store.detail(agent)
        rows=tuple(row for name in SCHEMAS if (row:=ToolCatalogStore(self.pool).get(name)) is not None and row.enabled and key in row.agents)
        if any(row.kind!='builtin' or row.exec or row.schema!=SCHEMAS[row.key] for row in rows):raise ChatUnavailable('Agent tool configuration unavailable')
        return agent,detail['prompt'],tuple(detail['visibility']['skill']),rows
    def resolve(self,request,conversation):
        snapshot=self.snapshot(conversation.agent_key);agent,prompt,skills,rows=snapshot
        identifier=agent.llm_profile_id or conversation.llm_profile_id or self.runtime.profile.id
        if not self.profile_available(identifier):raise ChatUnavailable('Profile unavailable')
        profile=self.runtime.profile
        identity=hmac.new(self.salt,json.dumps(asdict(profile),sort_keys=True).encode(),hashlib.sha256).hexdigest()
        model=Model(profile_id=identifier,name=profile.name,format=profile.format,model=profile.model,identity=identity,streaming=profile.streaming,window_tokens=max(32,min(1000,profile.context_window_k or 200))*1000)
        names={row.key for row in rows}
        def live():
            if self.snapshot(agent.key)!=snapshot or not self.profile_available(identifier):raise ChatUnavailable('Agent configuration changed')
        def binding(expected):
            live()
            if expected!=model:raise ChatUnavailable('Model changed')
            return ChatBinding(model,self.factory.build(profile))
        def gate(context,action):
            live();allowed=(action.kind=='model' and action.name=='complete') or action.kind=='tool' and action.name in names
            return Grant(allowed,allowed,allowed)
        def invoke(name,context,args):
            context.check();live()
            if name=='Skill':
                if args['name'] not in skills or args['name']!=SKILL:raise PermissionError('Skill not visible')
                text=SKILL_PATH.read_text()
                self.pool.execute(Query('INSERT INTO skill_usage(skill,agent_key,session_id,args_len,found) VALUES($1,$2,$3,0,true)',(SKILL,agent.key,'conv-'+str(conversation.id))))
                return text
            if name=='list_assessments':
                return json.dumps(dict(definitions=[v.public(self.runtime.enumeration) for v in DEFINITIONS.values()],assessments=[{k:r[k] for k in ('id','task_id','definition','origin','state')} for r in self.runtime.store.list_identities()]))
            if name=='read_assessment':
                record=self.runtime.store.for_task(args['task_id'])
                if record is None:raise ValueError('Assessment not found')
                report=record.get('report') or {}
                return json.dumps(dict(task_id=record['task_id'],state=record['state'],summary=report.get('summary'),run_id=report.get('run_id'),capabilities=report.get('capabilities'),url='/function/tasks/detail?id='+str(record['task_id'])))
            if name=='submit_assessment':
                origin=self.runtime.store.approved(args['url'])
                records=self.runtime.store.list_identities()
                prior=next((r for r in records if r['origin']==origin and r['credential_ref']),None)
                ref=prior['credential_ref'] if prior else ''
                result=next((r for r in records if not args.get('request_id') and r['origin']==origin and r['definition']==args['definition'] and r['version']==DEFINITIONS[args['definition']].version and r['credential_ref']==ref and r['state'] in ('pending','admitted','done')),None)
                if result is None:result=self.runtime.submit(args['definition'],origin,ref,source={'kind':'agent','agent_key':agent.key,'conversation_id':conversation.id},request_id=args.get('request_id',''))
                live()
                return json.dumps(dict(task_id=result['task_id'],assessment_id=result['id'],state=result['state'],url='/function/tasks/detail?id='+str(result['task_id'])))
            raise PermissionError('Unregistered tool')
        tools=tuple(Tool(row.key,row.description,row.schema,lambda c,a,n=row.key:invoke(n,c,a),lambda a,n=row.key:type(a)is dict and set(SCHEMAS[n]['required'])<=set(a)<=set(SCHEMAS[n]['properties'])) for row in rows)
        return ChatResources(self.transcript,model,binding,gate,self.transcript.finalized_result,tools=tools,system=self.render(prompt,{}),limits=Limits(max_turns=agent.max_turns or 8,max_seconds=agent.run_seconds or 600,max_tokens=64000))
    def register(self,router):
        self.catalog.register(router);self.service.register(router)
        router.add('GET','/api/skills',lambda request,params:json_response(200,{'skills':self.skill_catalog()}))
    def skill_catalog(self):
        count,tasks,agents=self.pool.fetch_one(Query("SELECT count(*),count(DISTINCT task_id),COALESCE(array_agg(DISTINCT agent_key) FILTER (WHERE agent_key IS NOT NULL),'{}') FROM skill_usage WHERE skill=$1 AND found",(SKILL,)))
        return [dict(name=SKILL,description='Assess public scripted REST operations on admitted ServiceNow instances.',files=['SKILL.md'],calls=count,tasks=tasks,usage_agents=agents)]
    def close(self):
        result=self.dispatcher.close(cancel=False,timeout=30)
        if not result.drained:raise RuntimeError('Assistant work remains')
        return asdict(result)
