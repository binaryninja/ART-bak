"""Company metadata and ownership lookup, with explicit optional company scope.

Ports Go companies.go metadata upsert, reads and ResolveCompanyWithICP priority:
exact root-domain, longest matching IP/CIDR, then normalized ICP; ties use lowest
company ID. Search is a new local literal name filter, not an external registry.
company_ids=None is trusted administrator scope; an empty sequence permits none.
Scope is immutable and also applies to transaction-bound resolution. Scoped
upsert only updates an existing permitted name-key; it cannot create companies.
create() supports empty initial scope only. Scope-rule edits and deletion fail
explicitly because they require global attribution recomputation/cleanup, not
merely FK updates. No automatic scope inference, PSL/DNS, network or migration.
Metadata writes are serialized using the same company advisory lock as assets.
Mock tests cover bound SQL/transaction contracts, not live PostgreSQL behavior.
"""
from dataclasses import dataclass,asdict,field
import ipaddress
from .core import Query,int64

LOCK=7337741003
COLS='id, name, nkey, logo, created_at::text, updated_at::text'
_GO_SPACE='\t\n\v\f\r \u0085\u00a0\u1680'+''.join(chr(x) for x in range(0x2000,0x200b))+'\u2028\u2029\u202f\u205f\u3000'


class CompanyInvalid(ValueError):pass
class CompanyNotFound(LookupError):pass
class CompanyNameConflict(ValueError):pass
class CompanyUnavailable(RuntimeError):pass


def _text(value):
    if not isinstance(value,str) or '\x00' in value:raise CompanyInvalid('Invalid company text')
    try:value.encode('utf-8')
    except UnicodeError:raise CompanyInvalid('Invalid company text') from None
    return value


def _positive(value):
    value=int64(value,'company id')
    if value<=0:raise CompanyInvalid('Company id must be positive')
    return value


def company_nkey(name):
    # Go Unicode simple lowercase differs from Python full lowercase expansion.
    value=''.join(ch.lower()[0] for ch in _text(name))
    words=[];word=''
    for ch in value:
        if ch in _GO_SPACE:
            if word:words.append(word);word=''
        else:word+=ch
    if word:words.append(word)
    return ' '.join(words)


def normalize_icp(value):
    return ''.join(ch.lower()[0] for ch in _text(value) if ch not in _GO_SPACE)


@dataclass
class Company:
    id:int
    name:str
    nkey:str
    logo:str|None
    created_at:str
    updated_at:str
    def to_dict(self):
        result=asdict(self)
        if self.logo is None:result.pop('logo')
        return result


@dataclass
class ScopeRule:
    id:int
    company_id:int
    kind:str
    domain:str
    net:str
    value:str
    raw:str
    reason:str
    def to_dict(self):
        result=asdict(self)
        for key in ('domain','net','value','reason'):
            if not result[key]:result.pop(key)
        return result


@dataclass
class CompanyWithScope(Company):
    asset_count:int=0
    scope:list[ScopeRule]=field(default_factory=list)
    def to_dict(self):
        result=super().to_dict()
        result['scope']=[rule.to_dict() for rule in self.scope]
        return result


class CompanyStore:
    def __init__(self,executor,*,company_ids=None):
        self.executor=executor
        if company_ids is None:self.company_ids=None
        else:
            if not isinstance(company_ids,(list,tuple,set,frozenset)):raise CompanyInvalid('Company scope must be a sequence')
            self.company_ids=tuple(sorted({_positive(value) for value in company_ids}))

    def _scope(self,params,column='id'):
        if self.company_ids is None:return ''
        params.append('{'+','.join(str(i) for i in self.company_ids)+'}')
        return f'{column}=ANY(${len(params)}::bigint[])'

    def _allowed(self,identifier):
        _positive(identifier)
        return self.company_ids is None or identifier in self.company_ids

    def _get(self,executor,condition,value):
        params=[value];scope=self._scope(params)
        rows=executor.fetch_all(Query('SELECT '+COLS+' FROM companies WHERE '+condition+(' AND '+scope if scope else ''),tuple(params)))
        if len(rows)>1:raise CompanyUnavailable('Duplicate company lookup result')
        if not rows:return None
        company=Company(*rows[0])
        if not self._allowed(company.id):raise CompanyUnavailable('Company lookup expanded ownership')
        return company

    def get(self,identifier):
        if not self._allowed(identifier):return None
        return self._get(self.executor,'id=$1',identifier)

    def get_by_name(self,name):
        return self._get(self.executor,'nkey=$1',company_nkey(name))

    def get_scope(self,identifier):
        if not self._allowed(identifier):return []
        rows=self.executor.fetch_all(Query("SELECT id, company_id, kind, COALESCE(domain,''), COALESCE(net::text,''), COALESCE(value,''), raw, COALESCE(reason,'') FROM company_scope WHERE company_id=$1 ORDER BY id",(identifier,)))
        result=[ScopeRule(*row) for row in rows]
        if any(row.company_id!=identifier for row in result):raise CompanyUnavailable('Scope lookup expanded ownership')
        return result

    def list(self,*,query=''):
        query=_text(query)
        params=[];conditions=[]
        scope=self._scope(params,'c.id')
        if scope:conditions.append(scope)
        if query:
            escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            params.append('%'+escaped+'%')
            conditions.append(f"c.name ILIKE ${len(params)} ESCAPE '\\'")
        sql='SELECT c.id,c.name,c.nkey,c.logo,c.created_at::text,c.updated_at::text,COUNT(DISTINCT a.id) FROM companies c LEFT JOIN assets a ON a.company_id=c.id'
        if conditions:sql+=' WHERE '+' AND '.join(conditions)
        sql+=' GROUP BY c.id ORDER BY c.name'
        companies=[CompanyWithScope(*row) for row in self.executor.fetch_all(Query(sql,tuple(params)))]
        for company in companies:
            if not self._allowed(company.id):raise CompanyUnavailable('Company query expanded ownership')
            company.scope=self.get_scope(company.id)
        return companies

    def search(self,query):return self.list(query=query)

    def _transaction(self):
        factory=getattr(self.executor,'transaction',None)
        if not callable(factory):raise CompanyUnavailable('Company writes require a transaction adapter')
        return factory()

    @staticmethod
    def _validate_metadata(name,logo):
        nkey=company_nkey(name);_text(logo)
        if not nkey:raise CompanyInvalid('Company name is required')
        return nkey

    def upsert(self,name,logo=''):
        nkey=self._validate_metadata(name,logo)
        with self._transaction() as tx:
            tx.execute(Query('SELECT pg_advisory_xact_lock($1)',(LOCK,)))
            if self.company_ids is not None:
                current=self._get(tx,'nkey=$1',nkey)
                if current is None:raise CompanyNotFound('Company is not in permitted scope')
                rows=tx.fetch_all(Query('UPDATE companies SET name=$1, logo=COALESCE($2,logo), updated_at=now() WHERE id=$3 RETURNING id',(name,logo or None,current.id)))
                if len(rows)!=1:raise CompanyNotFound('Company not found')
                if _positive(rows[0][0])!=current.id:raise CompanyUnavailable('Company mutation changed identity')
                return current.id,False
            rows=tx.fetch_all(Query('''INSERT INTO companies(name,nkey,logo) VALUES($1,$2,$3)
ON CONFLICT(nkey) DO UPDATE SET name=EXCLUDED.name,logo=COALESCE(EXCLUDED.logo,companies.logo),updated_at=now()
RETURNING id,(xmax=0)''',(name,nkey,logo or None)))
            if len(rows)!=1 or len(rows[0])!=2 or type(rows[0][1]) is not bool:raise CompanyUnavailable('Invalid company mutation result')
            return _positive(rows[0][0]),rows[0][1]

    def create(self,name,logo='',*,scope_inputs=()):
        nkey=self._validate_metadata(name,logo)
        if not isinstance(scope_inputs,(list,tuple)):raise CompanyInvalid('Scope inputs must be a sequence')
        if scope_inputs:raise CompanyUnavailable('Scope creation requires attribution recomputation')
        if self.company_ids is not None:raise CompanyUnavailable('Scoped stores cannot create new companies')
        with self._transaction() as tx:
            tx.execute(Query('SELECT pg_advisory_xact_lock($1)',(LOCK,)))
            rows=tx.fetch_all(Query('INSERT INTO companies(name,nkey,logo) VALUES($1,$2,$3) ON CONFLICT(nkey) DO NOTHING RETURNING id',(name,nkey,logo or None)))
            if not rows:raise CompanyNameConflict('Company name already exists')
            if len(rows)!=1:raise CompanyUnavailable('Invalid company mutation result')
            return _positive(rows[0][0])

    def resolve(self,root_domain='',ip='',icp=''):
        return self.resolve_with_executor(self.executor,root_domain,ip,icp)

    def resolve_with_executor(self,executor,root_domain='',ip='',icp=''):
        """Asset company_resolver callback; use caller's transaction/lock, no IO.

        Returning None means no matching rule, not an authorization to broaden
        asset scope. The asset mutation guard remains separately mandatory.
        """
        _text(root_domain);_text(ip);normalized=normalize_icp(icp)
        selectors=[]
        if root_domain:selectors.append(("kind='domain' AND domain=$1",root_domain,'length(domain) DESC, company_id'))
        if ip:
            try:
                if '%' in ip:raise ValueError('zone')
                ipaddress.ip_address(ip)
            except ValueError:pass  # Source skips malformed IP, still considers ICP.
            else:selectors.append(("kind IN ('ip','cidr') AND net >>= $1::inet",ip,'masklen(net) DESC, company_id'))
        if normalized:selectors.append(("kind='icp' AND value=$1",normalized,'company_id'))
        for condition,value,order in selectors:
            params=[value];scope=self._scope(params,'company_id')
            rows=executor.fetch_all(Query('SELECT company_id FROM company_scope WHERE '+condition+(' AND '+scope if scope else '')+' ORDER BY '+order+' LIMIT 1',tuple(params)))
            if rows:
                if len(rows)!=1 or len(rows[0])!=1:raise CompanyUnavailable('Invalid company resolver result')
                identifier=_positive(rows[0][0])
                if not self._allowed(identifier):raise CompanyUnavailable('Resolver expanded company scope')
                return identifier
        return None

    def delete(self,*args,**kwargs):raise CompanyUnavailable('Company deletion requires full attribution and asset cleanup')
    def update_scope(self,*args,**kwargs):raise CompanyUnavailable('Scope changes require global attribution recomputation')
    def add_scope(self,*args,**kwargs):raise CompanyUnavailable('Scope changes require global attribution recomputation')
