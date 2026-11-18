#!/usr/bin/env python3
"""Independently verify an already completed public REST assessment generation."""
import argparse,hashlib,json,os,urllib.request
from pathlib import Path
import psycopg
from artex.assessments.definitions import PUBLIC_REST
from artex.assessments.public_rest import REVIEWED
from artex.server.auth import sign_jwt
from artex.transcripts import TranscriptStore,messages

def require(value,message):
    if not value:raise AssertionError(message)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--task-id',required=True,type=int);parser.add_argument('--receipt',required=True,type=Path);parser.add_argument('--conversation-id',type=int);parser.add_argument('--transcript-root',type=Path);args=parser.parse_args()
    require(bool(args.conversation_id)==bool(args.transcript_root),'Conversation and transcript root must be supplied together')
    with psycopg.connect(os.environ['ARTEX_PG_DSN']) as fresh:
        row=fresh.execute("SELECT a.id,a.definition,a.version,a.origin,a.report,a.state,t.status,t.completed_at,b.run_id FROM artex_assessments a JOIN tasks t ON t.id=a.task_id JOIN artex_task_bootstrap b ON b.task_id=t.id WHERE t.id=%s",(args.task_id,)).fetchone()
        require(row is not None,'Assessment missing')
        identifier,definition,version,origin,report,state,status,completed,generation=row
        require((definition,version,state,status)==(PUBLIC_REST.key,PUBLIC_REST.version,'done','done') and completed is not None,'Assessment is not durably complete')
        require(report['run_id']==generation,'Report generation mismatch')
        rows=fresh.execute('SELECT operation,result,run_id FROM artex_assessment_observations WHERE assessment_id=%s',(identifier,)).fetchall()
        require({name for name,_,_ in rows}==set(PUBLIC_REST.operations) and all(run==generation for _,_,run in rows),'Observation generation mismatch')
        observations={name:value for name,value,_ in rows};result=observations['public_rest_probes']
        require(result.get('status')==200 and result.get('record_count')==len(result.get('records',[])),'Probe result missing')
        require(len(result['selected_ids'])==min(10,result['record_count']),'Selection coverage mismatch')
        operations={r['sys_id']:r for r in result['records']}
        require(set(result['selected_ids'])=={r['operation_id'] for r in result['requests']},'Selected operation lacks evidence')
        for request in result['requests']:
            require(request['operation_id'] in operations and request['authentication'] in ('none','guest_session'),'Request attribution mismatch')
            require('authorization' not in [n.lower() for n in request['request_header_names']],'Unexpected endpoint credential')
            require(len(request['body_sha256'])==64,'Response hash missing')
            if request['method']!='OPTIONS':
                operation=operations[request['operation_id']]
                require(any(p['route']==operation['operation_uri'] and p['method']==request['method'] and p['source_sha256']==request['source_sha256'] and p['body']==request['body'] for p in REVIEWED),'Handler probe lacks reviewed source')
        calls=fresh.execute('SELECT tool,status,run_id FROM artex_tool_calls WHERE task_id=%s',(args.task_id,)).fetchall()
        require(calls and all(state=='completed' and run==generation for _,state,run in calls),'Tool lifecycle incomplete')
        require({'run_assessment','finish_assessment'}<={name for name,_,_ in calls},'Assessment tools missing')
        providers=fresh.execute('SELECT worker,status,model FROM llm_records WHERE task_id=%s',(str(args.task_id),)).fetchall()
        require({'goals','planner','worker'}<={role for role,state,_ in providers if state=='ok'},'Shared provider execution missing')
        if args.conversation_id:
            terminal=fresh.execute("SELECT detail,is_error FROM conversation_activities WHERE conversation_id=%s AND kind='result' ORDER BY id DESC LIMIT 1",(args.conversation_id,)).fetchone()
            require(terminal and not terminal[1] and str(args.task_id) in terminal[0],'Agent result does not identify the task')
    conversation_evidence={}
    if args.conversation_id:
        records=TranscriptStore(str(args.transcript_root)).read('conv-'+str(args.conversation_id))
        blocks=[block for message in messages(records)[0] for block in message.get('content',[])]
        submission_calls=[b for b in blocks if b.get('type')=='tool_use' and b.get('name')=='submit_assessment' and b.get('input',{}).get('definition')==PUBLIC_REST.key]
        require(submission_calls,'Provider did not request the assessment tool')
        linked=[]
        for call in submission_calls:
            for block in blocks:
                if block.get('type')=='tool_result' and block.get('tool_use_id')==call['id']:
                    text=''.join(part.get('text','') for part in block.get('content',[]) if isinstance(part,dict))
                    try:value=json.loads(text)
                    except ValueError:continue
                    if str(value.get('task_id'))==str(args.task_id):linked.append(call['id'])
        require(linked,'Agent tool result is not linked to the task')
        conversation_evidence=dict(conversation_id=args.conversation_id,submission_tool_call_ids=linked)
    headers={'Authorization':'Bearer '+sign_jwt(bytes.fromhex(os.environ['ARTEX_SIGNING_KEY_HEX']))}
    def get(path):return json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8787/api'+path,headers=headers)))
    runtime=get('/assessments/runtime');detail=get('/tasks/'+str(args.task_id)+'/assessment?summary=1')
    require(not runtime['dispatcher_error'] and not runtime['pump_error'],'Runtime error')
    settlement=runtime['settlement']
    require(not settlement['busy'] and not any(settlement.get(k,0) for k in ('running','pending','deadline_owners','deadline_pending','continuation_owners','manager_only')),'Runtime has pending work')
    require(not any(detail['retained']),'Task generation still retained')
    receipt=dict(task_id=args.task_id,generation_id=generation,assessment_id=identifier,origin=origin,revision=report['revision'],source_sha256=report['source_sha256'],models=sorted({model for _,_,model in providers}),completed_at=completed.isoformat(),operations=result['record_count'],selected=len(result['selected_ids']),requests=len(result['requests']),tool_calls=len(calls),settlement=settlement,verified=True,**conversation_evidence)
    args.receipt.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))

if __name__=='__main__':main()
