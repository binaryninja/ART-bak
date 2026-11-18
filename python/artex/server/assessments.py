"""Generic assessment routes mounted on the existing authenticated task app."""
from dataclasses import asdict
from ..assessments.definitions import DEFINITIONS
from ..assessments.runtime import model_observations
from ..assessments.discovery import DiscoveryDispatcher
from ..db.core import Query
from .core import error_response,json_response

def register(runtime,app):
    router=app.router
    from contextlib import nullcontext
    from urllib.parse import parse_qsl,urlsplit
    from .command_history import CommandHistoryService
    from ..db.command_history import CommandHistoryStore,CommandScope
    class HistoryRoutes:
        def handle(self,operation,request):
            try:
                pairs=parse_qsl(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=4)
                requested=dict(pairs).get('task')
                if requested is None:scope=CommandScope('assessment-workspace')
                else:
                    if not requested.isascii() or not requested.isdigit() or not 0<int(requested)<2**63:raise ValueError()
                    rows=runtime.pool.fetch_all(Query('SELECT id FROM tasks WHERE exploration_id=$1 AND deleted_at IS NULL AND archived_at IS NULL',(int(requested),)))
                    if len(rows)!=1:return error_response(404,'Exploration history not found')
                    scope=CommandScope('assessment-workspace',rows[0][0],int(requested))
                store=CommandHistoryStore(runtime.pool,scope,authorize=lambda *_:True,current=lambda *_:True,lifecycle_guard=lambda *_:nullcontext(),raw_authorize=lambda *_:True)
                return CommandHistoryService(store,authorize=lambda *_:True,redact=runtime.redact).handle(operation,request)
            except (ValueError,TypeError):return error_response(400,'Invalid history selector')
        def register(self,router):CommandHistoryService.register(self,router)
    HistoryRoutes().register(router)
    def detail(record,summary=False):
        if record is None:return None
        value={k:v for k,v in record.items() if k!='credential_ref'}
        value['has_credentials']=bool(record['credential_ref'])
        value['task']=runtime.manager.task(str(record['task_id']))
        value['exploration_id']=runtime.pool.fetch_one(Query('SELECT exploration_id FROM tasks WHERE id=$1',(record['task_id'],)))[0]
        value['tool_calls']=dict(runtime.pool.fetch_all(Query('SELECT status,count(*) FROM artex_tool_calls WHERE task_id=$1 GROUP BY status',(record['task_id'],))))
        value['observations']=runtime.store.observations(record['id'])
        if summary:
            value['observations']=model_observations(value['observations'])
            if value.get('report'):value['report']={k:v for k,v in value['report'].items() if k!='observations'}
        value['extensions']=runtime.store.extension_results(record['id'])
        value['sources']=[row[0] for row in runtime.pool.fetch_all(Query('SELECT source FROM artex_assessment_sources WHERE assessment_id=$1',(record['id'],)))]
        value['sessions']=[dict(session_id=s,worker=w,calls=n) for s,w,n in runtime.pool.fetch_all(Query('SELECT session_id,worker,count(*) FROM llm_records WHERE task_id=$1 GROUP BY session_id,worker ORDER BY session_id',(str(record['task_id']),)))]
        value['retained']=runtime.manager.retirement_snapshot(str(record['task_id']))[2:]
        return value
    def request_data(request):
        data=request.json_object()
        if set(data)-{'definition','url','username','password','credential_ref','request_id'}:raise ValueError('Unexpected assessment fields')
        definition=data.get('definition','servicenow.inventory')
        if definition not in DEFINITIONS:raise ValueError('Unknown assessment definition')
        origin=runtime.store.approved(data['url'])
        username,password=data.get('username',''),data.get('password','')
        ref=data.get('credential_ref','')
        if username or password:
            if ref:raise ValueError('Choose supplied credentials or a credential reference')
            # Validate before storing; this constructs no connection.
            from ..recon.servicenow import ServiceNowInventory
            ServiceNowInventory(username,password,origin=origin,authorized_origins=runtime.store.allowed_origins)
            ref=runtime.credentials.put(origin,username,password)
        request_id=data.get("request_id","")
        if not isinstance(request_id,str) or len(request_id)>128:raise ValueError("Invalid request ID")
        return definition,origin,ref,request_id,bool(username and password)
    def handle(action,request,params):
        try:
            if action=='runtime':return json_response(200,dict(settlement=asdict(runtime.host.poll()),dispatcher_error=runtime.error,pump_error=runtime.bundle.pump.failure))
            if action=='definitions':return json_response(200,{'definitions':[d.public(runtime.enumeration) for d in DEFINITIONS.values()],'authorized_origins':sorted(runtime.store.allowed_origins)})
            if action=='list':return json_response(200,{'assessments':[detail(r,True) for r in runtime.store.list()],'dispatcher_error':runtime.error,'pump_error':runtime.bundle.pump.failure,'pump_detail':runtime.bundle.pump.failure_detail})
            if action=='create':
                definition,origin,ref,request_id,retry_authentication=request_data(request)
                record=runtime.submit(definition,origin,ref,request_id=request_id,retry_authentication=retry_authentication)
                return json_response(201,{'task_id':str(record['task_id']),'assessment_id':record['id']})
            if action=='detail':
                result=detail(runtime.store.for_task(params['id']),dict(parse_qsl(urlsplit(request.target).query)).get('summary')=='1')
                return json_response(200,result) if result else error_response(404,'Assessment not found')
            if action=='evidence':
                record=runtime.store.for_task(params['id'])
                result=runtime.store.observations(record['id']).get(params['operation']) if record else None
                return json_response(200,result) if result is not None else error_response(404,'Assessment evidence not found')
            if action=='discover':
                data=request.json_object()
                if set(data)-{'text','label'}:raise ValueError('Unexpected discovery fields')
                results=DiscoveryDispatcher(runtime).ingest(data['text'],data.get('label','uploaded text'))
                artifact=runtime.extensions.import_text(data['text'],redact=runtime.redact)
                return json_response(200,{'results':results,'artifact':artifact})
        except PermissionError:return error_response(403,'Assessment target or credential is outside scope')
        except (ValueError,KeyError,TypeError):return error_response(400,'Invalid assessment input')
        except Exception:return error_response(503,'Assessment outcome unavailable; inspect existing tasks before retrying')
    for method,path,action in [('GET','/api/assessments/runtime','runtime'),('GET','/api/assessment-definitions','definitions'),('GET','/api/assessments','list'),('POST','/api/assessments','create'),('POST','/api/assessments/discover','discover'),('GET','/api/tasks/{id}/assessment','detail'),('GET','/api/tasks/{id}/assessment/evidence/{operation}','evidence')]:
        router.add(method,path,lambda request,params,action=action:handle(action,request,params))
    from .tool_catalog import ToolCatalogService
    from ..db.tool_catalog import ToolCatalogStore
    from ..db.tool_catalog import ToolUnavailable
    class ReadCatalog:
        def list(self):return ToolCatalogStore(runtime.pool).list()
        def get(self,key):return ToolCatalogStore(runtime.pool).get(key)
        def usage_counts(self):return ToolCatalogStore(runtime.pool).usage_counts()
        def unavailable(self,*args,**kwargs):raise ToolUnavailable('Catalog changes require runtime reconfiguration')
        def update(self,tool,*,custom=True):
            from .assistant_host import SCHEMAS
            from ..db.tool_catalog import ToolInvalid
            from ..db.agents import AgentStore
            if custom or tool.key not in SCHEMAS:raise ToolUnavailable('Task tool changes require runtime reconfiguration')
            with runtime.assistants.lock:
                if tool.schema!=SCHEMAS[tool.key] or any((agent:=AgentStore(runtime.pool).get(name)) is None or agent.role!='assistant' for name in tool.agents):raise ToolInvalid('Unsupported assistant binding')
                return ToolCatalogStore(runtime.pool).update(tool,custom=False)
        create=delete=unavailable
    ToolCatalogService(ReadCatalog()).register(router)
    from .llm_records import LLMRecordService
    from ..db.llm_records import LLMRecordStore
    def record_task(selection):
        if selection.record_task:return selection.record_task
        if selection.task:return selection.task
        if selection.record_id:
            rows=runtime.pool.fetch_all(Query('SELECT task_id FROM llm_records WHERE id=$1',(selection.record_id,)))
            return rows[0][0] if rows else None
        return None
    def record_allowed(request,operation,selection):
        task=record_task(selection)
        return operation in ('list','tasks','record_metadata') or operation in ('models','detail','record_body') and task is not None and runtime.store.for_task(task) is not None
    def record_store(request,operation,selection):
        task=record_task(selection)
        return LLMRecordStore(runtime.pool,task_id=task)
    LLMRecordService(record_store,authorize=record_allowed,redact=lambda request,value,bodies:__import__('json').loads(runtime.redact(__import__('json').dumps(value)))).register(app.router)
    return app
