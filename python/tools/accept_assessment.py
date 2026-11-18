"""One real-provider assessment, independently verified from PostgreSQL."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
from urllib.request import Request,urlopen
from artex.server.auth import sign_jwt
from artex.assessments.definitions import SERVICENOW

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-origin',default='http://127.0.0.1:8787')
    parser.add_argument('--instance',required=True)
    parser.add_argument('--credential-ref',default='')
    parser.add_argument('--expected-access',choices=SERVICENOW.access_levels)
    parser.add_argument('--expected-extension',action='append',default=[])
    parser.add_argument('--expected-complete',action='append',default=[])
    parser.add_argument('--require-rest-source',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.api_origin!='http://127.0.0.1:8787':raise ValueError('Explicit local acceptance API required')
    token=sign_jwt(bytes.fromhex(os.environ['ARTEX_SIGNING_KEY_HEX']))
    def request(path,body=None):
        req=Request(args.api_origin+'/api'+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urlopen(req,timeout=30) as response:return json.load(response)
    receipt=request('/assessments',{'definition':SERVICENOW.key,'url':args.instance,'credential_ref':args.credential_ref,'request_id':str(uuid.uuid4())})
    task_id=receipt['task_id']
    deadline=time.monotonic()+4500
    while time.monotonic()<deadline:
        value=request('/tasks/'+task_id)
        if value['status'] in ('done','failed','timeout'):break
        time.sleep(1)
    else:raise RuntimeError('Assessment did not settle')
    if value['status']!='done':raise RuntimeError('Assessment failed')
    import psycopg
    with psycopg.connect(os.environ['ARTEX_PG_DSN']) as connection:
        task=connection.execute('SELECT status,completed_at,exploration_id FROM tasks WHERE id=%s',(int(task_id),)).fetchone()
        identifier,report=connection.execute('SELECT id,report FROM artex_assessments WHERE task_id=%s',(int(task_id),)).fetchone()
        observations=dict(connection.execute('SELECT operation,result FROM artex_assessment_observations WHERE assessment_id=%s',(identifier,)).fetchall())
        extensions={name:dict(arguments=arguments,result=result,run_id=generation) for name,arguments,result,generation in connection.execute('SELECT tool,arguments,result,run_id FROM artex_assessment_extension_results WHERE assessment_id=%s',(identifier,)).fetchall()}
        run_id,phase=connection.execute('SELECT run_id,phase FROM artex_task_bootstrap WHERE task_id=%s',(int(task_id),)).fetchone()
        records=connection.execute('SELECT id,worker,model,status,request_body,response_body FROM llm_records WHERE task_id=%s ORDER BY id',(task_id,)).fetchall()
        goals=connection.execute("SELECT state FROM exploration_nodes WHERE exploration_id=%s AND kind='goal'",(task[2],)).fetchall()
        ledger=connection.execute('SELECT c.tool,c.status,u.kind,r.kind,u.tool_use_id=r.tool_use_id,u.exploration_id=r.exploration_id,c.run_id,c.completed_at FROM artex_tool_calls c JOIN activity u ON u.id=c.use_activity_id LEFT JOIN activity r ON r.id=c.result_activity_id WHERE c.task_id=%s ORDER BY c.started_at',(int(task_id),)).fetchall()
        usage=connection.execute('SELECT tool_key,count(*) FROM tool_usage WHERE task_id=%s GROUP BY tool_key',(int(task_id),)).fetchall()
    assert task[0]=='done' and task[1] is not None
    assert set(observations)==set(SERVICENOW.operations) and report['observations']==observations
    for operation in args.expected_complete:assert observations[operation].get('completeness')=='complete',operation
    if args.require_rest_source:
        import hashlib
        operations={row['sys_id'] for row in observations['api_resources']['records']}
        sources={row['sys_id']:row for row in observations['api_scripts']['records']}
        assert operations==set(sources) and sources
        for row in sources.values():
            if row['operation_script_available']:assert hashlib.sha256(row['operation_script'].encode()).hexdigest()==row['stored_script_sha256']
            else:assert row['operation_script_unavailable_reason'] in ('protected','field_not_returned') and row['operation_script']=='' and row['stored_script_sha256'] is None
    assert report['extensions']==extensions
    assert set(args.expected_extension)<=set(report['required_extensions'])
    assert all(name in extensions and extensions[name]['arguments']==arguments and extensions[name]['run_id']==run_id for name,arguments in report['required_extensions'].items())
    assert run_id==report['run_id'] and phase=='prepared' and goals and all(row[0]=='met' for row in goals)
    if args.expected_access:assert report['capabilities']['access_level']==args.expected_access
    assert ledger and all(row[1]=='completed' and row[2:6]==('tool_use','tool_result',True,True) and row[6]==run_id and row[7] is not None for row in ledger)
    assert dict(usage).get('inspect_servicenow',0)>=len(SERVICENOW.operations)
    assert all(dict(usage).get(name,0)>=1 for name in report['required_extensions'])
    history=request('/commands?task='+str(task[2])+'&size=25');stats=request('/commands/stats?task='+str(task[2]))['stats']
    assert history['total']==len(ledger) and {'set_goals','add_intent','run_assessment','finish_assessment','inspect_servicenow'}<={row['tool'] for row in stats}
    assert sum(row['total'] for row in stats)==len(ledger)
    assert {'goals','planner','worker'} <= {row[1] for row in records if row[3]=='ok'}
    responses=[json.loads(row[5]) for row in records if row[1]=='worker' and row[3]=='ok']
    calls=[block for response in responses for block in response['message']['content'] if block['type']=='tool_use']
    assert any(block['name']=='run_assessment' for block in calls)
    assert any(block['name']=='finish_assessment' and block['input']['summary']==report['summary'] for block in calls)
    assert any(observations['home']['body_sha256'] in row[4] for row in records if row[1]=='worker')
    while time.monotonic()<deadline:
        settlement=request('/assessments/runtime')['settlement']
        if not any(request('/tasks/'+task_id+'/assessment?summary=1')['retained']) and not settlement['busy'] and all(settlement[key]==0 for key in ('running','pending','deadline_owners','deadline_pending','continuation_owners','manager_only')):break
        time.sleep(1)
    else:raise RuntimeError('Assessment ownership remains')
    evidence={'receipt':receipt,'revision':report['revision'],'source_sha256':report['source_sha256'],'run_id':run_id,
        'models':sorted({row[2] for row in records}),'record_ids':[row[0] for row in records],
        'task_status':task[0],'completed_at':task[1].isoformat(),'operations':{k:v.get('status') for k,v in observations.items()},'enumeration':{k:{field:v.get(field) for field in ('record_count','completeness','max_records','pagination_error','total_records_reported','source_available_count','source_unavailable_count','source_bytes')} for k,v in observations.items() if 'record_count' in v},
        'capabilities':report['capabilities'],'extensions':extensions,'extensions_revision':report['extensions_revision'],'tool_calls':len(ledger),'tool_usage':dict(usage),'settlement':settlement}
    args.output.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'task_id':task_id,'run_id':run_id,'status':'done','evidence':str(args.output)}))

if __name__=='__main__':main()
