"""Durable custom-agent event deliveries; uncertain admissions are never replayed."""
import json,time
from ..db.core import Query
from .core import Request

DDL='''CREATE TABLE IF NOT EXISTS artex_agent_events(id BIGSERIAL PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,task_id BIGINT,payload JSONB NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS artex_agent_deliveries(trigger_id BIGINT NOT NULL REFERENCES agent_triggers(id) ON DELETE CASCADE,event_id BIGINT NOT NULL REFERENCES artex_agent_events(id),state TEXT NOT NULL DEFAULT 'pending',conversation_id BIGINT REFERENCES conversations(id),activity_after BIGINT NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(trigger_id,event_id));
CREATE OR REPLACE FUNCTION artex_agent_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k TEXT; tid BIGINT; ek TEXT; p JSONB := '{}'::jsonb;
BEGIN
 IF TG_TABLE_NAME='tasks' THEN
   tid:=NEW.id;
   IF TG_OP='INSERT' THEN k:='task_create';ek:='task:'||NEW.id;
   ELSIF NEW.status='timeout' AND OLD.status IS DISTINCT FROM NEW.status THEN k:='task_timeout';ek:='timeout:'||NEW.id||':'||NEW.updated_at; END IF;
 ELSIF TG_TABLE_NAME='findings' THEN k:='finding';tid:=NEW.task_id;ek:='finding:'||NEW.id;p:=jsonb_build_object('finding_id',NEW.id);
 ELSIF TG_TABLE_NAME='exploration_nodes' THEN
   IF NEW.kind='goal' AND NEW.state='met' AND (TG_OP='INSERT' OR OLD.state IS DISTINCT FROM NEW.state) THEN SELECT id INTO tid FROM tasks WHERE exploration_id=NEW.exploration_id;k:='goal_met';ek:='goal:'||NEW.id||':'||NEW.updated_at;p:=jsonb_build_object('goal_id',NEW.id); END IF;
 ELSIF TG_TABLE_NAME='artex_tool_calls' THEN
   IF NEW.status='completed' AND OLD.status IS DISTINCT FROM NEW.status THEN k:='tool_call';tid:=NEW.task_id;ek:='tool:'||NEW.call_key;p:=jsonb_build_object('tool',NEW.tool,'run_id',NEW.run_id); END IF;
 END IF;
 IF k IS NOT NULL THEN INSERT INTO artex_agent_events(event_key,kind,task_id,payload) VALUES(ek,k,tid,p) ON CONFLICT DO NOTHING; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artex_agent_task_event ON tasks;
CREATE TRIGGER artex_agent_task_event AFTER INSERT OR UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION artex_agent_event();
DROP TRIGGER IF EXISTS artex_agent_finding_event ON findings;
CREATE TRIGGER artex_agent_finding_event AFTER INSERT ON findings FOR EACH ROW EXECUTE FUNCTION artex_agent_event();
DROP TRIGGER IF EXISTS artex_agent_goal_event ON exploration_nodes;
CREATE TRIGGER artex_agent_goal_event AFTER INSERT OR UPDATE ON exploration_nodes FOR EACH ROW EXECUTE FUNCTION artex_agent_event();
DROP TRIGGER IF EXISTS artex_agent_tool_event ON artex_tool_calls;
CREATE TRIGGER artex_agent_tool_event AFTER UPDATE ON artex_tool_calls FOR EACH ROW EXECUTE FUNCTION artex_agent_event();'''

MESSAGES={'finding':'finding_message','goal_met':'goal_message','task_timeout':'task_timeout_message','tool_call':'tool_call_message','task_create':'task_create_message','interval':'interval_message'}

class AgentEvents:
    def __init__(self,host):
        self.host,self.pool=host,host.pool
        self.pool.execute(Query("UPDATE artex_agent_deliveries SET state='uncertain',error='Host restarted after admission began' WHERE state IN ('admitting','running')"))
    def tick(self):
        host=self.host
        for agent in host.store.list():
            if not host.ready(agent.key):continue
            for trigger in host.management.triggers(agent)['triggers']:
                if not trigger['enabled']:continue
                identifier=trigger['id'];interval=trigger['interval_sec']
                if interval:
                    self.pool.execute(Query("INSERT INTO artex_agent_events(event_key,kind,payload) VALUES($1,'interval',$2::jsonb) ON CONFLICT DO NOTHING",('interval:'+str(identifier)+':'+str(int(time.time())//interval),json.dumps({'trigger_id':identifier}))))
                fields=[name.removeprefix('on_') for name,value in trigger.items() if name.startswith('on_') and value]
                if interval:fields.append('interval')
                for kind in fields:
                    self.pool.execute(Query("INSERT INTO artex_agent_deliveries(trigger_id,event_id) SELECT t.id,e.id FROM agent_triggers t JOIN artex_agent_events e ON e.created_at>=t.created_at WHERE t.id=$1 AND e.kind=$2 AND ($2<>'interval' OR e.payload->>'trigger_id'=$3) AND ($2<>'tool_call' OR t.tool_names='' OR e.payload->>'tool'=ANY(regexp_split_to_array(t.tool_names,'[,\\s]+'))) ON CONFLICT DO NOTHING",(identifier,kind,str(identifier))))
                self._settle(identifier)
                limit=1 if agent.trigger_run_mode=='serial' else agent.trigger_max_parallel
                active=self.pool.fetch_one(Query("SELECT count(*) FROM artex_agent_deliveries d JOIN agent_triggers t ON t.id=d.trigger_id WHERE t.agent_key=$1 AND d.state IN ('admitting','running','uncertain')",(agent.key,)))[0]
                if active>=limit:continue
                pending=self.pool.fetch_all(Query("SELECT d.event_id,e.kind,e.task_id,e.payload FROM artex_agent_deliveries d JOIN artex_agent_events e ON e.id=d.event_id WHERE d.trigger_id=$1 AND d.state='pending' ORDER BY e.id LIMIT 1",(identifier,)))
                if not pending:continue
                event,kind,task,payload=pending[0]
                conversation=None
                if agent.trigger_merge_mode!='none':
                    prior=self.pool.fetch_all(Query("SELECT d.conversation_id FROM artex_agent_deliveries d JOIN agent_triggers t ON t.id=d.trigger_id JOIN artex_agent_events e ON e.id=d.event_id WHERE t.agent_key=$1 AND d.state='done' AND ($2='all' OR e.task_id IS NOT DISTINCT FROM $3) ORDER BY d.event_id DESC LIMIT 1",(agent.key,agent.trigger_merge_mode,task)))
                    if prior and not host.dispatcher.running(prior[0][0]):conversation=host.conversations.get(prior[0][0])
                if conversation is None:conversation=host.conversations.create(agent.key,'Triggered: '+kind)
                baseline=self.pool.fetch_one(Query('SELECT COALESCE(max(id),0) FROM conversation_activities WHERE conversation_id=$1',(conversation.id,)))[0]
                self.pool.execute(Query("UPDATE artex_agent_deliveries SET state='admitting',conversation_id=$3,activity_after=$4 WHERE trigger_id=$1 AND event_id=$2 AND state='pending'",(identifier,event,conversation.id,baseline)))
                message=trigger[MESSAGES[kind]] or 'Review the recorded event using available scoped tools.'
                message+='\nEvent: '+json.dumps(dict(kind=kind,task_id=task,**payload),sort_keys=True)
                response=host.service.handle(Request('POST','/api/conversations/'+str(conversation.id)+'/messages',{'Idempotency-Key':'event:'+str(identifier)+':'+str(event)},json.dumps({'message':message}).encode()),str(conversation.id))
                self.pool.execute(Query('UPDATE artex_agent_deliveries SET state=$3,error=$4 WHERE trigger_id=$1 AND event_id=$2',(identifier,event,'running' if response.status==202 else 'uncertain',None if response.status==202 else 'Admission HTTP '+str(response.status))))
                self.pool.execute(Query('UPDATE agent_triggers SET last_fire=now() WHERE id=$1',(identifier,)))
    def _settle(self,identifier):
        for event,conversation,after in self.pool.fetch_all(Query("SELECT event_id,conversation_id,activity_after FROM artex_agent_deliveries WHERE trigger_id=$1 AND state='running'",(identifier,))):
            if self.host.dispatcher.running(conversation):continue
            rows=self.pool.fetch_all(Query("SELECT is_error FROM conversation_activities WHERE conversation_id=$1 AND id>$2 AND kind='result' ORDER BY id DESC LIMIT 1",(conversation,after)))
            state='done' if rows and not rows[0][0] else 'failed' if rows else 'uncertain'
            self.pool.execute(Query('UPDATE artex_agent_deliveries SET state=$3 WHERE trigger_id=$1 AND event_id=$2',(identifier,event,state)))
