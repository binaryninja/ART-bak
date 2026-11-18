"""Reusable, evidence-backed assessment tools bound to one admitted task."""
import json
import hashlib
from copy import deepcopy
from ..agent.runtime import Tool
from ..db.core import Query
from ..db.tool_catalog import CatalogTool
from ..db.exploration import ExplorationStore
from ..db.findings import FindingStore
from ..tools.mcp import _validate
from .definitions import DEFINITIONS,capabilities

EMPTY={'type':'object','properties':{},'additionalProperties':False}
SCHEMAS={
 'assessment_scope':EMPTY,'assessment_capabilities':EMPTY,'list_assessment_checks':EMPTY,
 'list_assets':{'type':'object','properties':{'limit':{'type':'integer','minimum':1,'maximum':100}},'additionalProperties':False},
 'read_assessment_evidence':{'type':'object','properties':{'operation':{'type':'string','maxLength':128},'offset':{'type':'integer','minimum':0,'maximum':1000000},'limit':{'type':'integer','minimum':1,'maximum':25},'script_offset':{'type':'integer','minimum':0,'maximum':1048576},'script_limit':{'type':'integer','minimum':1,'maximum':32768}},'required':['operation'],'additionalProperties':False},
 'report_finding':{'type':'object','properties':{'name':{'type':'string','minLength':1,'maxLength':200},'summary':{'type':'string','minLength':1,'maxLength':4096},'severity':{'type':'string','enum':['critical','high','medium','low']},'operations':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'string','maxLength':128}}},'required':['name','summary','severity','operations'],'additionalProperties':False},
}
DESCRIPTIONS={'assessment_scope':'Read the exact authorized target and credential availability.',
 'assessment_capabilities':'Read observed identity, roles, capabilities, and limitations.',
 'list_assessment_checks':'List registered checks and their required capabilities.',
 'list_assets':'List bounded asset metadata for the admitted target.',
 'read_assessment_evidence':'Read a bounded page of already persisted check evidence.',
 'report_finding':'Record a finding backed by successful persisted checks; review status starts pending.'}
CATALOG=tuple(CatalogTool(k,True,DESCRIPTIONS[k],deepcopy(s),['worker'],True,'builtin',{},False) for k,s in SCHEMAS.items())

def bind(runtime,scope,record,check,guard):
    def execute(name,context,args):
        with guard():
            check(context);observations=runtime.store.observations(record['id'])
            if name=='assessment_scope':result=dict(task_id=str(scope.task_id),assessment_id=record['id'],origin=record['origin'],definition=record['definition'],version=record['version'],has_credentials=bool(record['credential_ref']),read_only=True)
            elif name=='assessment_capabilities':result=capabilities(observations,runtime.inventory(record).username)
            elif name=='list_assessment_checks':result=DEFINITIONS[record['definition']].public(runtime.enumeration)
            elif name=='list_assets':
                host=record['origin'].removeprefix('https://')
                rows=runtime.pool.fetch_all(Query("SELECT id,type,domain,url,technologies FROM assets WHERE lower(domain)=$1 OR lower(split_part(split_part(url,'://',2),'/',1))=$1 ORDER BY id LIMIT $2",(host,args.get('limit',50))))
                result={'assets':[dict(zip(('id','type','domain','url','technologies'),row)) for row in rows]}
            elif name=='read_assessment_evidence':
                if args['operation'] not in DEFINITIONS[record['definition']].operations:raise ValueError('Unregistered check')
                result=deepcopy(observations.get(args['operation'],{'error':'not_collected'}))
                if len(result.get('pages',[]))>2:
                    pages=result['pages'];result.update(stored_page_count=len(pages),pages_sha256=hashlib.sha256(json.dumps(pages,sort_keys=True).encode()).hexdigest(),pages=[pages[0],pages[-1]],page_sample=True)
                if 'records' in result:
                    offset=args.get('offset',0);limit=args.get('limit',10);rows=result['records']
                    result.update(records=rows[offset:offset+limit],stored_record_count=len(rows),offset=offset)
                    if 'requests' in result:
                        selected={row.get('sys_id') for row in result['records']}
                        result['stored_request_count']=len(result['requests'])
                        result['requests']=[{k:v for k,v in request.items() if k!='body_sample'} for request in result['requests'] if request.get('operation_id') in selected]
                    for row in result['records']:
                        if 'operation_script' in row:
                            source=row['operation_script'];start=args.get('script_offset',0);end=start+args.get('script_limit',8192)
                            row.update(operation_script=source[start:end],script_offset=start,next_script_offset=min(end,len(source)) if end<len(source) else None)
                    while result['records'] and len(json.dumps(result).encode())>60000:result['records'].pop()
                    result['next_offset']=offset+len(result['records']) if offset+len(result['records'])<len(rows) else None
                for nested in ('rest_acls','roles','access_policies'):
                    child=result.get(nested)
                    if isinstance(child,dict) and isinstance(child.get('records'),list):
                        rows=child['records'];offset=args.get('offset',0);end=offset+args.get('limit',10)
                        child.update(records=rows[offset:end],stored_record_count=len(rows),next_offset=end if end<len(rows) else None)
            elif name=='report_finding':
                selected=sorted(set(args['operations']))
                if any(k not in observations or observations[k].get('status')!=200 for k in selected):raise ValueError('Successful persisted evidence required')
                evidence=json.dumps({'assessment_id':record['id'],'operations':selected})
                summary=runtime.redact(args['summary']);name_value=runtime.redact(args['name'])
                with runtime.pool.transaction() as tx:
                    graph=ExplorationStore(tx,scope.exploration_id)
                    node=graph.add_node('finding',{'summary':summary,'name':name_value,'severity':args['severity'],'evidence':evidence},5,'confirmed','assessment')
                    graph.link(scope.intent_id,'yields',node)
                    identifier=FindingStore(tx,task_id=scope.task_id,exploration_id=scope.exploration_id).create(task_id=scope.task_id,node_id=node,name=name_value,severity=args['severity'],summary=summary,evidence=evidence,worker=scope.owner)
                    check(context)
                result={'finding_id':str(identifier),'node_id':str(node),'status':'pending'}
            else:raise ValueError('Unknown assessment tool')
            if name=='list_assets':
                while result['assets'] and len(json.dumps(result,default=str).encode())>60000:result['assets'].pop();result['output_limited']=True
            check(context);return runtime.redact(json.dumps(result,default=str))
    def validate(name,args):_validate(args,SCHEMAS[name]);return True
    return tuple(Tool(k,DESCRIPTIONS[k],deepcopy(s),lambda c,a,k=k:execute(k,c,a),lambda a,k=k:validate(k,a)) for k,s in SCHEMAS.items())
