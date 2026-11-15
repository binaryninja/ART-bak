"""Bounded read-only local planner graph view and source coldgraph algorithms.

Maps agent/coldgraph.go and graphOverviewData in agent/tools.go (there is no
source tools_overview.go implementation), plus tools_digest.go restoration views.
Loads actual ExplorationStore nodes/edges under explicit run/revision authority
and a transaction parent SHARE lock. This non-mutating view requires a writable
transaction/locking privilege; PostgreSQL READ ONLY transactions reject FOR SHARE.
All graph writers must use that parent
barrier for a consistent snapshot; ordinary independent SQL snapshots are not a
distributed guarantee. Overflow fails rather than silently misclassifying heat.
No tool registration, grants, provider, SQL writes, stamps, digests or completion.
Pure stamp/group/signature helpers propose values only, never persist them.
No inherited union, asset/site coverage, traffic refs or per-worker budget view.
These unavailable sections are named explicitly. Summary text is capped160 and
output bounded; existing digest bodies are displayed, never synthesized.
"""
from dataclasses import dataclass
from collections import Counter,defaultdict
from hashlib import sha256
import json
from ..db.core import Query,int64
from ..db.exploration import ExplorationStore

class ContextUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class ContextScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str

@dataclass(frozen=True)
class ColdBlock:
    members:tuple
    anchors:tuple

def live(node):return node.kind=='intent' and node.state in ('open','running','paused')

class ColdGraph:
    def __init__(self,nodes,edges):
        self.nodes={n.id:n for n in nodes};self.children=defaultdict(set);self.parents=defaultdict(set)
        for edge in edges:
            if edge.rel=='covers' or edge.from_id not in self.nodes or edge.to_id not in self.nodes:continue
            self.children[edge.from_id].add(edge.to_id);self.parents[edge.to_id].add(edge.from_id)
    def hot(self):
        hot=set();stack=[n.id for n in self.nodes.values() if live(n)]
        while stack:
            id=stack.pop()
            if id in hot:continue
            hot.add(id);stack.extend(self.parents[id])
        for n in self.nodes.values():
            if live(n):hot.update(self.children[n.id])
        return hot
    def structural_cold(self):
        hot=self.hot();return {n.id for n in self.nodes.values() if n.kind in ('fact','intent') and n.id not in hot}
    def groups(self,eligible,minimum=2):
        if type(minimum) is not int or minimum<1:raise ValueError('Positive block minimum required')
        selected=set(eligible)
        if not selected<=self.structural_cold():raise ValueError('Only structurally cold nodes may be grouped')
        parent={id:id for id in selected}
        def find(id):
            while parent[id]!=id:parent[id]=parent[parent[id]];id=parent[id]
            return id
        def union(a,b):parent[find(a)]=find(b)
        def components():
            groups=defaultdict(list)
            for id in sorted(selected):groups[find(id)].append(id)
            return list(groups.values())
        for id in selected:
            for child in self.children[id]&selected:union(id,child)
        siblings=defaultdict(list)
        for component in components():
            if len(component)!=1:continue
            id=component[0]
            for p in self.parents[id]:
                if self.nodes[p].kind!='digest':siblings[p].append(id)
        for ids in siblings.values():
            for id in ids[1:]:union(ids[0],id)
        result=[]
        for ids in components():
            if len(ids)<minimum:continue
            members=set(ids);anchors={p for id in ids for p in self.parents[id] if p not in members and self.nodes[p].kind!='digest'}
            result.append(ColdBlock(tuple(sorted(ids)),tuple(sorted(anchors))))
        return sorted(result,key=lambda b:b.members[0])

def stamp_ops(structural,cold_since,round_no):
    if type(round_no) is not int or round_no<0:raise ValueError('Invalid round')
    operations=[(id,True,round_no) for id in structural if cold_since.get(id) is None]
    operations.extend((id,False,0) for id,value in cold_since.items() if value is not None and id not in structural)
    return sorted(operations)

def eligible_cold(structural,cold_since,round_no,debounce=6):
    if type(debounce) is not int or debounce<0 or type(round_no) is not int or round_no<0:raise ValueError('Invalid cold threshold')
    return {id for id in structural if cold_since.get(id) is not None and round_no-cold_since[id]>=debounce}

def block_signature(block,versions):
    raw=''.join(f'm:{id}:{versions.get(id,0)};' for id in block.members)+''.join(f'a:{id}:{versions.get(id,0)};' for id in block.anchors)
    return sha256(raw.encode()).hexdigest()

class GraphContext:
    def __init__(self,store,scope,*,current,lifecycle_guard,max_nodes=5000,max_edges=5000,max_bytes=1048576):
        if not isinstance(store,ExplorationStore) or store.read_only or not isinstance(scope,ContextScope):raise ValueError('Explicit local graph scope required')
        for id in (scope.task_id,scope.exploration_id,scope.run_id):
            if int64(id,'scope ID')<=0:raise ValueError('Positive scope ID required')
        if scope.exploration_id!=store.exploration_id or not isinstance(scope.revision,str) or not scope.revision or len(scope.revision)>256:raise ValueError('Scope mismatch')
        if not callable(current) or not callable(lifecycle_guard):raise ValueError('Lifecycle authority required')
        if any(type(v) is not int or not 1<=v<=9999 for v in (max_nodes,max_edges)) or type(max_bytes) is not int or not 1<=max_bytes<=16777216:raise ValueError('Invalid view bounds')
        self.store,self.scope=store,scope;self.current,self.guard=current,lifecycle_guard
        self.max_nodes,self.max_edges,self.max_bytes=max_nodes,max_edges,max_bytes
    def _check(self):
        if self.current(self.scope) is not True:raise ContextUnavailable('Graph context lifetime changed')
    def _load(self):
        with self.guard(self.scope):
            self._check()
            with self.store.executor.transaction() as tx:
                rows=tx.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',(self.scope.task_id,self.scope.exploration_id)))
                if rows not in ([(self.scope.task_id,)],[[self.scope.task_id]]):raise ContextUnavailable('Local graph parent unavailable')
                graph=ExplorationStore(tx,self.scope.exploration_id)
                nodes=graph.nodes(self.max_nodes+1);edges=graph.edges(self.max_edges+1)
                if len(nodes)>self.max_nodes or len(edges)>self.max_edges:raise ContextUnavailable('Graph exceeds complete snapshot bound')
                self._check()
                return nodes,edges
    @staticmethod
    def _compact(node):
        p=node.payload if isinstance(node.payload,dict) else {};text=p.get('summary',p.get('text',''))
        if not isinstance(text,str):text=''
        out={'id':node.id,'state':node.state,'summary':text[:160]}
        if len(text)>160:out.update(summary=text[:160]+'…',summary_truncated=True)
        if isinstance(p.get('confidence'),str):out['confidence']=p['confidence'][:32]
        return out
    def _bounded(self,out):
        if len(json.dumps(out,ensure_ascii=False,allow_nan=False).encode())>self.max_bytes:raise ContextUnavailable('Graph view exceeds output bound')
        return out
    def snapshot(self):
        nodes,edges=self._load();cold=ColdGraph(nodes,edges);hot=cold.hot();byid={n.id:n for n in nodes}
        digests=[n for n in nodes if n.kind=='digest' and n.state=='active'];members={d.id:sorted(e.to_id for e in edges if e.rel=='covers' and e.from_id==d.id) for d in digests}
        covered={id for ids in members.values() for id in ids};hidden=covered-hot
        recent=sorted(nodes,key=lambda n:n.id,reverse=True)
        parents=defaultdict(list);yields=defaultdict(list);origins={}
        for edge in sorted(edges,key=lambda e:(e.from_id,e.rel,e.to_id)):
            if edge.rel in ('derived_from','spawns'):parents[edge.to_id].append(edge.from_id)
            elif edge.rel=='yields':yields[edge.from_id].append(edge.to_id);origins[edge.to_id]=edge.from_id
        def compact(values):
            out=[]
            for n in values:
                entry=self._compact(n);payload=n.payload if isinstance(n.payload,dict) else {}
                if n.kind in ('goal','hint'):entry['text']=entry.pop('summary')
                if n.kind=='intent':
                    if parents[n.id]:entry['parents']=parents[n.id]
                    if yields[n.id]:entry['yields']=yields[n.id]
                    assets=payload.get('asset_ids',payload.get('target_ids'))
                    if assets is None and payload.get('target_id') not in (None,''):assets=[payload['target_id']]
                    if assets is not None:entry['asset_ids']=assets
                elif n.id in origins:entry['from_intent']=origins[n.id]
                out.append(entry)
            return out
        intents=[n for n in nodes if n.kind=='intent'];done=[n for n in recent if n.kind=='intent' and n.state in ('done','blocked','exhausted')]
        ordered=sorted(digests,key=lambda n:(-max(members[n.id],default=0),n.id))
        shown=[]
        for n in ordered[:15]:
            body=n.payload.get('body','') if isinstance(n.payload,dict) else ''
            if not isinstance(body,str):raise ContextUnavailable('Invalid persisted digest body')
            shown.append({'id':n.id,'body':body,'member_count':len(members[n.id])})
        out=dict(scope={'task_id':self.scope.task_id,'exploration_id':self.scope.exploration_id,'run_id':self.scope.run_id,'revision':self.scope.revision},
                 goals=compact([n for n in recent if n.kind=='goal'][:100]),hints=compact([n for n in recent if n.kind=='hint'][:50]),
                 open_intents=compact(sorted([n for n in intents if n.state=='open'],key=lambda n:(-n.priority,n.id))[:30]),
                 running_intents=compact([n for n in recent if n.kind=='intent' and n.state=='running']),
                 recent_done_intents=compact([n for n in done if n.id not in hidden][:12]),done_intents_total=len(done),frontier_open=sum(n.state=='open' for n in intents),
                 facts=sum(n.kind=='fact' for n in nodes),recent_facts=compact([n for n in recent if n.kind=='fact' and n.id not in hidden][:20]),
                 findings_total=sum(n.kind=='finding' for n in nodes),finding_list=compact([n for n in recent if n.kind=='finding'][:10]),
                 cold_digests=shown,cold_digests_more=[n.id for n in ordered[15:]],hot_ids=sorted(hot),
                 unavailable=['task_root','inherited_sources','asset_site_coverage','traffic_refs','worker_budgets','cold_stamp_persistence','digest_generation'])
        if not out['cold_digests_more']:out.pop('cold_digests_more')
        if not out['cold_digests']:out.pop('cold_digests')
        return self._bounded(out)
    def expand_digest(self,id):
        if int64(id,'digest ID')<=0:raise ValueError('Positive digest ID required')
        nodes,edges=self._load();byid={n.id:n for n in nodes};digest=byid.get(id)
        if digest is None or digest.kind!='digest':raise ContextUnavailable('Local digest unavailable')
        members=sorted(e.to_id for e in edges if e.from_id==id and e.rel=='covers')
        body=digest.payload.get('body','') if isinstance(digest.payload,dict) else ''
        if not isinstance(body,str):raise ContextUnavailable('Invalid persisted digest body')
        return self._bounded({'id':id,'state':digest.state,'body':body,'members':[self._compact(byid[m]) if m in byid else {'id':m,'missing':True} for m in members]})
