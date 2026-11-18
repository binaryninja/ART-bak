"""Origin-bound ServiceNow login sessions; cookies never leave the host adapter."""
from email.message import Message
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from types import SimpleNamespace
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request
import re


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.fields={}; self.found=False

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='form':
            self.active=attrs.get('action') in ('login.do','/login.do') and attrs.get('method','').lower()=='post'
            self.found |= self.active
        if self.active and tag=='input' and attrs.get('type')=='hidden' and attrs.get('name') in (
                'sysparm_ck','ni.nolog.user_password','ni.noecho.user_name','ni.noecho.user_password','screensize'):
            self.fields[attrs['name']]=attrs.get('value','')

    def handle_endtag(self, tag):
        if tag=='form':self.active=False


class LoginSession:
    def __init__(self, origin, username, password, request, remember):
        self.origin,self.username,self.password=origin,username,password
        self.request,self.remember=request,remember
        self.cookies=CookieJar();self.token='';self.attempted=False;self.ready=False

    def headers(self, path, headers):
        req=Request(self.origin+path,headers=headers)
        self.cookies.add_cookie_header(req)
        if self.token:req.add_header('X-UserToken',self.token)
        return dict(req.header_items())

    def receive(self, path, headers):
        message=Message()
        for name,value in headers:message.add_header(name,value)
        self.cookies.extract_cookies(SimpleNamespace(info=lambda:message),Request(self.origin+path))
        for cookie in self.cookies:self.remember(cookie.value)

    def login(self, context):
        if self.attempted:return self.ready
        self.attempted=True
        page=self.request(context,'GET','/login.do',{'Accept':'text/html'},session=True)
        if page['status']!=200 or len(page['body'])>262144:return False
        form=LoginForm();form.feed(page['body'].decode('utf-8','replace'))
        if not form.found:return False
        body=urlencode(dict(form.fields,user_name=self.username,user_password=self.password,sys_action='sysverb_login')).encode()
        page=self.request(context,'POST','/login.do',{'Accept':'text/html','Content-Type':'application/x-www-form-urlencoded'},body=body,session=True)
        for _ in range(5):
            if page['status'] not in (301,302,303):break
            location=dict((k.lower(),v) for k,v in page['headers']).get('location','')
            target=urlsplit(urljoin(self.origin+'/login.do',location))
            if target.scheme+'://'+target.netloc!=self.origin or target.fragment or target.path not in ('/','/login.do','/login_redirect.do','/navpage.do','/welcome.do'):
                return False
            path=target.path+('?' + target.query if target.query else '')
            page=self.request(context,'GET',path,{'Accept':'text/html'},session=True)
        if page['status']!=200 or len(page['body'])>262144:return False
        match=re.search(r"\bg_ck\s*=\s*['\"]([A-Za-z0-9_-]{1,256})['\"]",page['body'].decode('utf-8','replace'))
        if match:self.token=match.group(1);self.remember(self.token)
        self.ready=True
        return True
