"""Durable assessment requests, observations and admission identity."""
import hashlib
import json
import uuid
from ..db.core import Query
from ..db.tasks import TaskStore,TaskCreateOptions
from .definitions import DEFINITIONS,instance_origin

DDL='''CREATE TABLE IF NOT EXISTS artex_assessment_denials (credential_ref TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS artex_asset_discovery_events (
 id BIGSERIAL PRIMARY KEY,asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
 source_version TIMESTAMPTZ NOT NULL);
ALTER TABLE artex_asset_discovery_events DROP CONSTRAINT IF EXISTS artex_asset_discovery_events_asset_id_source_version_key;
CREATE TABLE IF NOT EXISTS artex_asset_discovery_receipts (
 source_key TEXT NOT NULL,event_id BIGINT NOT NULL REFERENCES artex_asset_discovery_events(id) ON DELETE CASCADE,
 PRIMARY KEY(source_key,event_id));
CREATE OR REPLACE FUNCTION artex_queue_asset_discovery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO artex_asset_discovery_events(asset_id,source_version) VALUES(NEW.id,NEW.updated_at);
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artex_asset_discovery ON assets;
CREATE TRIGGER artex_asset_discovery AFTER INSERT OR UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION artex_queue_asset_discovery();
INSERT INTO artex_asset_discovery_events(asset_id,source_version) SELECT a.id,a.updated_at FROM assets a WHERE NOT EXISTS (SELECT 1 FROM artex_asset_discovery_events e WHERE e.asset_id=a.id);
CREATE TABLE IF NOT EXISTS artex_assessments (
 id TEXT PRIMARY KEY, dedup_key TEXT UNIQUE NOT NULL, definition TEXT NOT NULL,
 version INTEGER NOT NULL, origin TEXT NOT NULL, credential_ref TEXT NOT NULL DEFAULT '',
 task_id BIGINT UNIQUE REFERENCES tasks(id), state TEXT NOT NULL DEFAULT 'pending',
 report JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS artex_assessment_sources (
 assessment_id TEXT NOT NULL REFERENCES artex_assessments(id), source_key TEXT NOT NULL,
 source JSONB NOT NULL, PRIMARY KEY(assessment_id,source_key));
CREATE TABLE IF NOT EXISTS artex_assessment_observations (
 assessment_id TEXT NOT NULL REFERENCES artex_assessments(id), operation TEXT NOT NULL,
 result JSONB NOT NULL, run_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(assessment_id,operation));'''
COLS='id,definition,version,origin,credential_ref,task_id,state,report'

def decode(row):
    if row is None:return None
    value=dict(zip(COLS.split(','),row))
    if isinstance(value['report'],str):value['report']=json.loads(value['report'])
    return value

class AssessmentStore:
    def __init__(self,executor,allowed_origins):
        self.executor=executor
        self.allowed_origins=frozenset(instance_origin(x) for x in allowed_origins)

    def approved(self,url):
        origin=instance_origin(url)
        if origin not in self.allowed_origins:raise PermissionError('Instance is outside the authorized scope')
        return origin

    def get(self,identifier):
        rows=self.executor.fetch_all(Query('SELECT '+COLS+' FROM artex_assessments WHERE id=$1',(identifier,)))
        return decode(rows[0]) if rows else None

    def for_task(self,task_id):
        rows=self.executor.fetch_all(Query('SELECT '+COLS+' FROM artex_assessments WHERE task_id=$1',(int(task_id),)))
        return decode(rows[0]) if rows else None

    def list(self):
        return [decode(row) for row in self.executor.fetch_all(Query('SELECT '+COLS+' FROM artex_assessments ORDER BY created_at DESC LIMIT 200'))]

    def submit(self,definition,url,credential_ref,source,profile_id,request_id=""):
        spec=DEFINITIONS[definition];origin=self.approved(url)
        key=hashlib.sha256(json.dumps([definition,spec.version,origin,credential_ref,request_id],separators=(',',':')).encode()).hexdigest()
        source_json=json.dumps(source,sort_keys=True)
        source_key=hashlib.sha256(source_json.encode()).hexdigest()
        with self.executor.transaction() as tx:
            tx.execute(Query('INSERT INTO artex_assessments(id,dedup_key,definition,version,origin,credential_ref) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(dedup_key) DO NOTHING',
                (str(uuid.uuid4()),key,definition,spec.version,origin,credential_ref)))
            record=decode(tx.fetch_one(Query('SELECT '+COLS+' FROM artex_assessments WHERE dedup_key=$1 FOR UPDATE',(key,))))
            tx.execute(Query('INSERT INTO artex_assessment_sources(assessment_id,source_key,source) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING',(record['id'],source_key,source_json)))
            if record['task_id'] is None:
                description=f"{spec.name}: {origin} [assessment {record['id']}]"
                goal='Complete the registered read-only inventory, persist evidence for every permitted operation, and report observed access and limitations.'
                task=TaskStore(tx).create(description,goal,TaskCreateOptions(name=spec.name,llm_profile_ids=[profile_id],coverage_enabled=False))
                tx.execute(Query('UPDATE artex_assessments SET task_id=$2 WHERE id=$1',(record['id'],task.id)))
                tx.execute(Query("INSERT INTO task_scope(task_id,kind,domain,source,reason) VALUES($1,'subdomain',$2,'manual','Explicit assessment instance scope') ON CONFLICT DO NOTHING",(task.id,origin.removeprefix('https://'))))
                record['task_id']=task.id
        return record

    def observations(self,identifier):
        return {name:(json.loads(value) if isinstance(value,str) else value) for name,value in self.executor.fetch_all(Query('SELECT operation,result FROM artex_assessment_observations WHERE assessment_id=$1 ORDER BY created_at,operation',(identifier,)))}

    def observe(self,identifier,operation,result,run_id):
        self.executor.execute(Query('INSERT INTO artex_assessment_observations(assessment_id,operation,result,run_id) VALUES($1,$2,$3::jsonb,$4) ON CONFLICT DO NOTHING',(identifier,operation,json.dumps(result),run_id)))

    def state(self,identifier,state):
        self.executor.execute(Query('UPDATE artex_assessments SET state=$2 WHERE id=$1',(identifier,state)))
