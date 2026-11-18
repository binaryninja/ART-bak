"""Protected persisted catalog HTTP; no automatic tool/agent registration.

GET list reports source configuration DTO, not execution readiness. Use explicit
execution_ready(key) with a host readiness resolver; stored enabled is insufficient.
Builtin main/planner/worker and retester are never advertised executable by this
adapter. POST creates custom metadata and an English starter prompt transactionally;
PATCH changes custom display metadata only. No provider or code is invoked.
GET detail includes persisted prompt/history/visibility and public profile labels;
known special-key wrapup defaults require explicit code-default resolver rather
than invented generic values. No execution capability follows from a prompt.
Config/prompt/visibility/delete/trigger mutation and preview remain explicit503.
"""
from copy import deepcopy
import json
from urllib.parse import urlsplit
from .core import error_response,json_response
from ..db.agents import Agent,AgentInvalid,AgentMissing,AgentUnavailable,AgentConflict,key,metadata

GLOBAL_VARS=[dict(name='Now',description='Current server time, refreshed for each run.',example='2026-08-11 14:30:00 CST',source='runtime'),
             dict(name='DataDir',description='Server data root; task and conversation artifacts use scoped subdirectories.',example='/app/data',source='runtime')]
GENERIC_WRAPUP='Your budget is about to expire. Persist completed results that have not yet been saved, then summarize what you did and the key conclusions in one standalone plain-text sentence.'


def _pairs(items):
    result={}
    for k,v in items:
        if k in result:raise AgentInvalid('Duplicate JSON field')
        result[k]=v
    return result


class AgentService:
    def __init__(self,store=None,*,readiness=None,detail_defaults=None,management=None):
        self.store,self.readiness,self.detail_defaults=store,readiness,detail_defaults
        self.management=management
    def execution_ready(self,identifier):
        if self.store is None or not callable(self.readiness):return False
        agent=self.store.get(key(identifier))
        if agent is None or not agent.enabled or agent.key=='retester' or agent.role!='assistant':return False
        return self.readiness(deepcopy(agent)) is True
    def register(self,router):
        for method,path,action in [('GET','/api/agents','list'),('POST','/api/agents','create'),
            ('GET','/api/agents/{key}','get'),('PATCH','/api/agents/{key}','patch'),('DELETE','/api/agents/{key}','unavailable')]:
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))
        for method,suffix in [('PUT','config'),('PUT','prompt'),('POST','prompt/reset'),('PUT','wrapup'),
            ('POST','wrapup/reset'),('PUT','wrapup/task-timeout'),('POST','wrapup/task-timeout/reset'),
            ('GET','triggers'),('POST','triggers'),('GET','prompts'),('GET','variables'),
            ('POST','prompt/preview'),('GET','visibility'),('PUT','visibility')]:
            router.add(method,'/api/agents/{key}/'+suffix,lambda req,params,a=method+' '+suffix:self.handle(a,req,params))
        if self.management is not None:self.management.register(router)
    def _defaults(self,agent):
        if callable(self.detail_defaults):value=self.detail_defaults(deepcopy(agent))
        elif agent.key in ('worker','planner','mainagent'):raise AgentUnavailable('Code defaults resolver required')
        else:value=dict(wrapup_default=GENERIC_WRAPUP,wrapup_max_turns_default=10,
                        task_timeout_wrapup_default='',task_timeout_wrapup_max_turns_default=10)
        names={'wrapup_default','wrapup_max_turns_default','task_timeout_wrapup_default','task_timeout_wrapup_max_turns_default'}
        if not isinstance(value,dict) or set(value)!=names:raise AgentUnavailable('Invalid code defaults')
        if any(not isinstance(value[n],str) for n in names if n.endswith('default') and 'turns' not in n):raise AgentUnavailable('Invalid code defaults')
        if any(type(value[n]) is not int or value[n]<0 for n in names if 'turns' in n):raise AgentUnavailable('Invalid code defaults')
        return deepcopy(value)
    def handle(self,action,request,params):
        try:
            if self.management is not None and (action=='unavailable' or ' ' in action):return self.management.handle(action,request,params)
            if urlsplit(request.target).query:raise AgentInvalid('Unexpected query')
            identifier=key(params['key']) if 'key' in params else None
            if self.store is None or action=='unavailable' or ' ' in action:raise AgentUnavailable()
            if action=='list':
                agents=self.store.list();mcp,skills,tools=self.store.binding_counts()
                return json_response(200,{'agents':[dict(a.to_dict(mcp_count=mcp.get(a.id,0),skill_count=skills.get(a.id,0),tool_count=tools.get(a.key,0)),**({'execution_ready':self.execution_ready(a.key)} if self.management is not None else {})) for a in agents]})
            agent=self.store.get(identifier) if identifier else None
            if identifier and agent is None:raise AgentMissing()
            if action=='get':
                defaults=self._defaults(agent);value=self.store.detail(agent)
                value['variables']=[v for v in value['variables'] if v['name'] not in ('Now','DataDir')]+deepcopy(GLOBAL_VARS)
                return json_response(200,dict(value,agent=agent.to_dict(),**defaults,
                    wrapup_prompt=agent.wrapup_prompt,wrapup_max_turns=agent.wrapup_max_turns,
                    task_timeout_wrapup_prompt=agent.task_timeout_wrapup_prompt,
                    task_timeout_wrapup_max_turns=agent.task_timeout_wrapup_max_turns,
                    task_timeout_wrapup_supported=bool(defaults['task_timeout_wrapup_default'])))
            if len(request.body)>131072:return error_response(413,'Request body too large')
            body=json.loads(request.body.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(AgentInvalid()))
            if not isinstance(body,dict) or set(body)-({'key','name','description'} if action=='create' else {'name','description'}):raise AgentInvalid()
            name,description=metadata(body.get('name'),body.get('description',''))
            if action=='create':
                newkey=body.get('key')
                if not isinstance(newkey,str):raise AgentInvalid()
                return json_response(200,self.store.create(key(newkey.strip()),name,description).to_dict())
            if agent.builtin:raise AgentInvalid('Builtin metadata is immutable')
            self.store.update_metadata(identifier,name,description)
            return json_response(200,{'ok':True})
        except AgentConflict:return error_response(409,'Agent key already exists')
        except AgentMissing:return error_response(404,'Agent not found')
        except AgentUnavailable:return error_response(503,'Agent capability unavailable')
        except (ValueError,UnicodeError):return error_response(400,'Invalid agent request')
        except Exception:return error_response(500,'Agent operation failed')
