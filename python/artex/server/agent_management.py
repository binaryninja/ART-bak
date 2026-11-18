"""Editable custom-agent configuration with host-validated capabilities."""
import json
from ..db.agents import AgentInvalid,AgentMissing,AgentUnavailable,key
from ..db.core import Query
from .core import error_response,json_response

CONFIG={'enabled':bool,'max_turns':int,'run_seconds':int,'web_search':bool,'interactive_shell':bool,'llm_profile_id':(int,type(None)),'trigger_run_mode':str,'trigger_merge_mode':str,'trigger_max_parallel':int}
TRIGGER={'enabled':bool,'interval_sec':int,**{n:bool for n in ('on_finding','on_goal_met','on_task_timeout','on_tool_call','on_task_create')},**{n:str for n in ('interval_message','finding_message','goal_message','task_timeout_message','tool_call_message','task_create_message','tool_names')}}

class AgentManagement:
    def __init__(self,store,*,profile_available,tool_names,skills,render,defaults,lock):
        self.store,self.pool=store,store.executor
        self.profile_available,self.tool_names,self.skills=profile_available,frozenset(tool_names),frozenset(skills)
        self.render,self.defaults,self.lock=render,defaults,lock
    def register(self,router):
        for method in ('PATCH','DELETE'):
            router.add(method,'/api/triggers/{id}',lambda request,params:self.trigger_route(request,params))
    def agent(self,identifier):
        value=self.store.get(key(identifier))
        if value is None:raise AgentMissing()
        return value
    def editable(self,agent):
        if agent.builtin or agent.role!='assistant':raise AgentUnavailable('Built-in task configuration is host-owned')
    def config(self,agent,data):
        self.editable(agent)
        if not data or set(data)-CONFIG.keys() or any(type(v) not in (t if isinstance(t,tuple) else (t,)) for k,v in data.items() for t in (CONFIG[k],)):raise AgentInvalid()
        if any(data.get(k,False) for k in ('web_search','interactive_shell')):raise AgentInvalid('Capability has no registered host adapter')
        for k,lo,hi in (('max_turns',0,100),('run_seconds',0,3600),('trigger_max_parallel',1,8)):
            if k in data and not lo<=data[k]<=hi:raise AgentInvalid()
        if data.get('trigger_run_mode','serial') not in ('serial','parallel') or data.get('trigger_merge_mode','all') not in ('all','by_task','none'):raise AgentInvalid()
        if data.get('llm_profile_id') is not None and not self.profile_available(data['llm_profile_id']):raise AgentInvalid('Profile unavailable')
        self.pool.execute(Query('UPDATE agents SET '+','.join(k+'=$'+str(i+2) for i,k in enumerate(data))+' WHERE id=$1',(agent.id,*data.values())))
        return {'ok':True}
    def prompt(self,agent,data):
        self.editable(agent)
        if set(data)-{'template','note'} or not isinstance(data.get('template'),str) or not data['template'].strip() or len(data['template'].encode())>65536 or not isinstance(data.get('note',''),str):raise AgentInvalid()
        self.render(data['template'],{})
        with self.pool.transaction() as tx:
            tx.fetch_one(Query('SELECT id FROM agents WHERE id=$1 FOR UPDATE',(agent.id,)))
            row=tx.fetch_one(Query("INSERT INTO agent_prompts(agent_id,version,template_text,note,updated_by) SELECT $1,COALESCE(MAX(version),0)+1,$2,$3,'operator' FROM agent_prompts WHERE agent_id=$1 RETURNING id,version",(agent.id,data['template'],data.get('note',''))))
            tx.execute(Query('UPDATE agents SET current_prompt_id=$2 WHERE id=$1',(agent.id,row[0])))
        return {'version':row[1]}
    def visibility(self,agent,data=None):
        if data is None:
            value=self.store.detail(agent)['visibility'];value['tools']=[r[0] for r in self.pool.fetch_all(Query('SELECT key FROM tools WHERE agents ? $1 AND enabled ORDER BY key',(agent.key,)))];return value
        self.editable(agent)
        if set(data)-{'mcp','skill','tools'} or data.get('mcp',[]) or not isinstance(data.get('skill',[]),list) or not set(data.get('skill',[]))<=self.skills:raise AgentInvalid('Unregistered visible capability')
        names=data.get('tools')
        if names is not None and (not isinstance(names,list) or not set(names)<=self.tool_names):raise AgentInvalid('Unregistered tool')
        with self.pool.transaction() as tx:
            tx.execute(Query('DELETE FROM agent_visibility WHERE agent_id=$1',(agent.id,)))
            tx.execute(Query('DELETE FROM agent_skill_visibility WHERE agent_id=$1',(agent.id,)))
            for name in set(data.get('skill',[])):tx.execute(Query('INSERT INTO agent_skill_visibility(agent_id,skill_name,enabled) VALUES($1,$2,true)',(agent.id,name)))
            if names is not None:
                for name in self.tool_names:
                    tx.execute(Query("UPDATE tools SET agents=(SELECT COALESCE(jsonb_agg(v),'[]'::jsonb) FROM jsonb_array_elements(agents) v WHERE v<>to_jsonb($1::text)) WHERE key=$2",(agent.key,name)))
                    if name in names:tx.execute(Query("UPDATE tools SET agents=agents || jsonb_build_array($1::text) WHERE key=$2",(agent.key,name)))
        return {'ok':True}
    def triggers(self,agent):
        fields=('id','agent_key',*TRIGGER,'last_fire')
        rows=self.pool.fetch_all(Query('SELECT '+','.join(fields)+' FROM agent_triggers WHERE agent_key=$1 ORDER BY id',(agent.key,)))
        return {'triggers':[dict(zip(fields,[v.isoformat() if hasattr(v,'isoformat') else v for v in row])) for row in rows]}
    def validate_trigger(self,data):
        if not data or set(data)-TRIGGER.keys() or any(type(v) is not TRIGGER[k] for k,v in data.items()):raise AgentInvalid()
        if 'interval_sec' in data and data['interval_sec']!=0 and not 30<=data['interval_sec']<=31536000:raise AgentInvalid()
        if any(isinstance(v,str) and len(v.encode())>8192 for v in data.values()):raise AgentInvalid()
    def trigger_route(self,request,params):
        try:
            identifier=int(params['id'])
            with self.lock:
                rows=self.pool.fetch_all(Query('SELECT agent_key FROM agent_triggers WHERE id=$1',(identifier,)))
                if not rows:return error_response(404,'Trigger not found')
                self.editable(self.agent(rows[0][0]))
                if request.method=='DELETE':self.pool.execute(Query('DELETE FROM agent_triggers WHERE id=$1',(identifier,)));return json_response(200,{'deleted':identifier})
                data=request.json_object();self.validate_trigger(data)
                self.pool.execute(Query('UPDATE agent_triggers SET '+','.join(k+'=$'+str(i+2) for i,k in enumerate(data))+' WHERE id=$1',(identifier,*data.values())))
                return json_response(200,{'ok':True})
        except (ValueError,TypeError):return error_response(400,'Invalid trigger')
    def handle(self,action,request,params):
        with self.lock:
            agent=self.agent(params['key']);data=request.json_object() if request.body else {}
            if action=='PUT config':result=self.config(agent,data)
            elif action=='PUT prompt':result=self.prompt(agent,data)
            elif action=='POST prompt/reset':result=self.prompt(agent,dict(template=self.defaults(agent.key),note='Reset default'))
            elif action=='GET prompts':result={'versions':self.store.detail(agent)['versions']}
            elif action=='GET variables':result={'variables':self.store.detail(agent)['variables']}
            elif action=='POST prompt/preview':result={'rendered':self.render(data['template'],data.get('sample',{}))}
            elif action=='GET visibility':result=self.visibility(agent)
            elif action=='PUT visibility':result=self.visibility(agent,data)
            elif action=='GET triggers':result=self.triggers(agent)
            elif action=='POST triggers':
                self.editable(agent);self.validate_trigger(data)
                row=self.pool.fetch_one(Query('INSERT INTO agent_triggers(agent_key,'+','.join(data)+') VALUES('+','.join('$'+str(i+1) for i in range(len(data)+1))+') RETURNING id',(agent.key,*data.values())))
                result=next(r for r in self.triggers(agent)['triggers'] if r['id']==row[0])
            elif action=='unavailable' and request.method=='DELETE':
                self.editable(agent)
                self.pool.execute(Query('UPDATE agents SET enabled=false WHERE id=$1',(agent.id,)))
                self.pool.execute(Query('UPDATE agent_triggers SET enabled=false WHERE agent_key=$1',(agent.key,)))
                result={'deleted':agent.key}
            else:raise AgentUnavailable()
            return json_response(200,result)
