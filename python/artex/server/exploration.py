"""Read-only exploration HTTP contracts from server/server.go and dto.go.

An injected resolve_store(task_id: str) must return the authorized, live task's
ExplorationStore or None. An optional active_task_id() supplies the Go active-task
fallback; no resolver or active selection is inferred. Store ownership is checked
before reads, and direct inherited stores must be read-only and identify their
source task. Authentication remains Router's protected default.

Graph/frontier/intent reads preserve source limits and immutable direct-history
filtering. /nodes deliberately returns 503: paginated node-board persistence,
neighbour lookup and anchored-asset DTO hydration are not ported. No invented
node/edge detail routes, writes, workers, deletion or listener are provided.
Invalid/duplicate/unknown query parameters fail rather than silently selecting a
different task. Explicit missing tasks return 404 rather than Go's empty success.
Inherited paging is bounded to 10000 pages and fails explicitly at that bound.
JSON payload text is reserialized from the DB object (semantic, not byte parity).
"""
import json
import re
from urllib.parse import parse_qs, urlsplit
from .core import Router, Request, json_response, error_response
from ..db.exploration import Node, Edge


class ExplorationUnavailable(RuntimeError): pass
class _Invalid(ValueError): pass
class _Missing(LookupError): pass


def _number(raw, name, *, minimum=1, maximum=2**63-1):
    if not isinstance(raw,str) or not re.fullmatch(r'[0-9]{1,19}',raw):
        raise _Invalid('Invalid '+name)
    result=int(raw)
    if not minimum <= result <= maximum: raise _Invalid('Invalid '+name)
    return result


def node_dto(node, *, source_task_id=0):
    if not isinstance(node,Node): raise ExplorationUnavailable('Invalid graph node')
    result={'id':str(node.id),'type':node.kind,
            'payload':json.dumps(node.payload,ensure_ascii=False,allow_nan=False,separators=(',',':')),
            'priority':node.priority,'state':node.state,'origin':node.origin,
            'ts':node.created_at.replace(microsecond=0).isoformat().replace('+00:00','Z')}
    source=source_task_id or node.source_task_id
    if source: result['source_task_id']=str(source)
    if source_task_id or node.inherited: result['inherited']=True
    if node.delete_reason: result['delete_reason']=node.delete_reason
    return result


def edge_dto(edge):
    if not isinstance(edge,Edge): raise ExplorationUnavailable('Invalid graph edge')
    return {'src':str(edge.from_id),'dst':str(edge.to_id),'rel':edge.rel}


class ExplorationService:
    def __init__(self, resolve_store=None, *, active_task_id=None):
        self.resolve_store=resolve_store
        self.active_task_id=active_task_id

    def register(self,router:Router):
        for path,action in (
            ('/api/exploration/frontier','frontier'),
            ('/api/exploration/intents','intents'),
            ('/api/exploration/graph','graph'),
            ('/api/exploration/nodes','nodes'),
        ):
            router.add('GET',path,lambda request,params,action=action:self._handle(action,request))

    @staticmethod
    def _require(store,*methods):
        if any(not callable(getattr(store,name,None)) for name in methods):
            raise ExplorationUnavailable('Exploration persistence capability is unavailable')

    def _store(self,q):
        if not callable(self.resolve_store): raise ExplorationUnavailable('Task exploration resolver is unavailable')
        if 'task' in q:
            task_id=str(_number(q['task'],'task ID'))
        else:
            if not callable(self.active_task_id): raise ExplorationUnavailable('Active task selection is unavailable')
            task_id=self.active_task_id()
            if not task_id: return None
            task_id=str(_number(task_id,'active task ID'))
        store=self.resolve_store(task_id)
        if store is None: raise _Missing()
        self._require(store,'task_id')
        owner=store.task_id()
        if type(owner) is not int or owner!=int(task_id):
            raise ExplorationUnavailable('Task exploration ownership mismatch')
        if getattr(store,'read_only',False): raise ExplorationUnavailable('Task store is unexpectedly inherited')
        return store

    def _sources(self,store):
        self._require(store,'direct_sources')
        sources=store.direct_sources()
        seen=set()
        for source in sources:
            other=source.store
            self._require(other,'task_id')
            if (type(source.task_id) is not int or source.task_id<=0 or source.task_id in seen
                or not getattr(other,'read_only',False)
                or other.source_task_id!=source.task_id or other.task_id()!=source.task_id
                or other.exploration_id!=source.exploration_id):
                raise ExplorationUnavailable('Invalid inherited exploration scope')
            seen.add(source.task_id)
        return sources

    def _handle(self,action,request:Request):
        try:
            try:
                qraw=parse_qs(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=16,errors='strict')
            except (ValueError,UnicodeError) as error:
                raise _Invalid('Invalid query encoding') from error
            allowed={'task'} | ({'limit'} if action=='frontier' else {'limit','before','page'} if action=='intents' else {'page','size','kind','state','q','order'} if action=='nodes' else set())
            if set(qraw)-allowed or any(len(value)!=1 for value in qraw.values()):
                raise _Invalid('Unknown or repeated query parameter')
            q={key:value[0] for key,value in qraw.items()}
            limit=_number(q.get('limit','100' if action=='frontier' else '300'),'limit',maximum=500)
            before=_number(q.get('before','0'),'before',minimum=0)
            if 'page' in q: _number(q['page'],'page')
            store=self._store(q)
            if action=='nodes': raise ExplorationUnavailable('Paginated node board and anchor hydration are not implemented')
            if store is None:
                return json_response(200, {'nodes':[],'edges':[]} if action=='graph' else {'items':[],'has_more':False} if action=='intents' and ('page' in q or 'before' in q) else [])
            if action=='frontier':
                self._require(store,'frontier')
                return json_response(200,[node_dto(n) for n in store.frontier(limit)])
            if action=='graph': return json_response(200,self._graph(store))
            if 'before' not in q and 'page' not in q:
                self._require(store,'list_by_kind')
                return json_response(200,[node_dto(n) for n in store.list_by_kind('intent',limit)])
            self._require(store,'list_by_kind_page')
            nodes,more=store.list_by_kind_page('intent',before,limit)
            pairs=[(node,0) for node in nodes]
            for source in self._sources(store):
                rows,source_more=self._history(source.store,before,limit)
                pairs.extend((node,source.task_id) for node in rows)
                more=more or source_more
            pairs.sort(key=lambda pair:pair[0].id,reverse=True)
            more=bool(more or len(pairs)>limit)
            return json_response(200,{'items':[node_dto(n,source_task_id=src) for n,src in pairs[:limit]],'has_more':more})
        except _Missing: return error_response(404,'Task not found')
        except _Invalid: return error_response(400,'Invalid exploration query')
        except ExplorationUnavailable: return error_response(503,'Exploration capability unavailable')
        except Exception: return error_response(500,'Exploration read failed')

    def _graph(self,store):
        self._require(store,'nodes','edges')
        nodes=[node_dto(n) for n in store.nodes(2000)]
        edges=[edge_dto(e) for e in store.edges(5000)]
        for source in self._sources(store):
            self._require(source.store,'nodes','edges')
            inherited=[n for n in source.store.nodes(2000) if n.kind!='intent' or n.state in ('done','blocked','exhausted','stopped')]
            visible={n.id for n in inherited}
            nodes.extend(node_dto(n,source_task_id=source.task_id) for n in inherited)
            edges.extend(edge_dto(e) for e in source.store.edges(5000) if e.from_id in visible and e.to_id in visible)
        return {'nodes':nodes,'edges':edges}

    def _history(self,store,before,limit):
        self._require(store,'list_by_kind_page')
        rows=[]; cursor=before
        for _ in range(10000):
            page,more=store.list_by_kind_page('intent',cursor,max(limit,300))
            for node in page:
                if node.state in ('done','blocked','exhausted','stopped'):
                    rows.append(node)
                    if len(rows)>limit: return rows[:limit],True
            if not more or not page: return rows,False
            next_cursor=page[-1].id
            if next_cursor<=0 or (cursor and next_cursor>=cursor):
                raise ExplorationUnavailable('Nonadvancing inherited cursor')
            cursor=next_cursor
        raise ExplorationUnavailable('Inherited history page budget exceeded')
