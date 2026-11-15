"""Read-only node-board persistence from exploration.go and digest.go.

Wrap an explicitly authorized ExplorationStore; no task selection, writes or
connections occur. Page/count follow upstream defaults (page >= 1, size 20..200)
and payload/origin ILIKE plus numeric/#id search. Wildcards retain Go semantics.
Count and page are separate queries, not a repeatable-read snapshot guarantee.

Additional isolation: edge endpoints must both belong to the captured scope.
Inherited stores expose only immutable intent results, including in neighbor and
anchor lookups; other kinds retain upstream inherited-graph visibility. Scope is
captured at construction, not changed by later mutation of the supplied store.
No asset DTO hydration or authorization beyond this supplied scope is inferred.
"""
from dataclasses import dataclass
import re
from .core import Query, int64
from .exploration import ExplorationStore, Node, Edge, NODE_COLS, GraphInvalid

_GO_SPACE='\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
MAX_IDS=10000


@dataclass(frozen=True)
class NodeFilter:
    kinds: tuple[str,...]=()
    states: tuple[str,...]=()
    query: str=''
    asc: bool=False


def _positive(value,name):
    value=int64(value,name)
    if value<=0: raise GraphInvalid(name+' must be positive')
    return value


def _text(value):
    if not isinstance(value,str) or '\x00' in value: raise GraphInvalid('filter values must be text without NUL')
    try: value.encode('utf-8')
    except UnicodeError as error: raise GraphInvalid('invalid UTF-8 filter') from error
    return value


def _visible(alias=''):
    prefix=alias+'.' if alias else ''
    return f"({prefix}kind <> 'intent' OR {prefix}state IN ('done','blocked','exhausted','stopped'))"


class NodeBoardStore:
    def __init__(self,store:ExplorationStore):
        if not isinstance(store,ExplorationStore): raise GraphInvalid('an explicit ExplorationStore is required')
        self.executor=store.executor
        self.exploration_id=_positive(store.exploration_id,'exploration ID')
        if type(store.read_only) is not bool: raise GraphInvalid('invalid read-only flag')
        self.read_only=store.read_only
        self.source_task_id=_positive(store.source_task_id,'source task ID') if self.read_only else 0

    def _node(self,row):
        node=Node(*row)
        if self.read_only: node.inherited=True;node.source_task_id=self.source_task_id
        return node

    @staticmethod
    def page_bounds(page,size):
        page=max(1,int64(page,'page'))
        size=min(200,size) if int64(size,'size')>0 else 20
        offset=int64((page-1)*size,'offset')
        return page,size,offset

    def nodes_page(self,filter:NodeFilter|None=None,page=1,size=20):
        filter=NodeFilter() if filter is None else filter
        if not isinstance(filter,NodeFilter) or type(filter.asc) is not bool: raise GraphInvalid('invalid node filter')
        _,size,offset=self.page_bounds(page,size)
        args=[self.exploration_id];conds=['exploration_id=$1']
        if self.read_only: conds.append(_visible())
        for column,values in (('kind',filter.kinds),('state',filter.states)):
            if not isinstance(values,(list,tuple)) or len(values)>100: raise GraphInvalid('invalid node filter values')
            if values:
                marks=[]
                for value in values:
                    args.append(_text(value));marks.append('$'+str(len(args)))
                conds.append(column+' IN ('+','.join(marks)+')')
        query=_text(filter.query).strip(_GO_SPACE)
        if query:
            args.append('%'+query+'%');mark='$'+str(len(args))
            ors=['payload::text ILIKE '+mark,"COALESCE(origin,'') ILIKE "+mark]
            numeric=query.removeprefix('#')
            if re.fullmatch(r'[+-]?[0-9]+',numeric):
                digits=numeric.lstrip('+-').lstrip('0') or '0'
                number=int(('-' if numeric.startswith('-') else '')+digits) if len(digits)<=19 else 2**63
                if -(2**63)<=number<2**63:
                    args.append(number);ors.append('id = $'+str(len(args)))
            conds.append('('+' OR '.join(ors)+')')
        where=' WHERE '+' AND '.join(conds)
        row=self.executor.fetch_one(Query('SELECT COUNT(*) FROM exploration_nodes'+where,tuple(args)))
        if not row or len(row)!=1 or type(row[0]) is not int or row[0]<0: raise RuntimeError('invalid node count')
        total=row[0]
        args.extend((size,offset));order='ASC' if filter.asc else 'DESC'
        sql='SELECT '+NODE_COLS+' FROM exploration_nodes'+where+' ORDER BY id '+order+f' LIMIT ${len(args)-1} OFFSET ${len(args)}'
        return [self._node(row) for row in self.executor.fetch_all(Query(sql,tuple(args)))],total

    def _ids(self,ids):
        if not isinstance(ids,(list,tuple)) or len(ids)>MAX_IDS: raise GraphInvalid('invalid node ID list')
        values=list(dict.fromkeys(_positive(value,'node ID') for value in ids))
        return (self.exploration_id,*values),','.join('$'+str(i+2) for i in range(len(values)))

    def nodes_by_ids(self,ids):
        args,marks=self._ids(ids)
        if not marks: return []
        sql='SELECT '+NODE_COLS+' FROM exploration_nodes WHERE exploration_id=$1 AND id IN ('+marks+')'
        if self.read_only: sql+=' AND '+_visible()
        return [self._node(row) for row in self.executor.fetch_all(Query(sql+' ORDER BY id',args))]

    def edges_touching(self,ids):
        args,marks=self._ids(ids)
        if not marks: return []
        sql='''SELECT e.src_id, e.rel, e.dst_id FROM exploration_edges e
JOIN exploration_nodes src ON src.id=e.src_id AND src.exploration_id=e.exploration_id
JOIN exploration_nodes dst ON dst.id=e.dst_id AND dst.exploration_id=e.exploration_id
WHERE e.exploration_id=$1 AND (e.src_id IN ('''+marks+') OR e.dst_id IN ('+marks+'))'
        if self.read_only: sql+=' AND '+_visible('src')+' AND '+_visible('dst')
        return [Edge(*row) for row in self.executor.fetch_all(Query(sql,args))]

    def node_assets(self,ids):
        args,marks=self._ids(ids)
        if not marks: return {}
        sql='''SELECT a.node_id, a.asset_id FROM exploration_anchors a
JOIN exploration_nodes n ON n.id=a.node_id
WHERE n.exploration_id=$1 AND a.node_id IN ('''+marks+')'
        if self.read_only: sql+=' AND '+_visible('n')
        result={}
        for row in self.executor.fetch_all(Query(sql,args)):
            if len(row)!=2 or row[0] not in args[1:]: raise RuntimeError('invalid node asset row')
            result.setdefault(_positive(row[0],'node ID'),[]).append(_positive(row[1],'asset ID'))
        return result
