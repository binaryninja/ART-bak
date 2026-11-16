"""Compose three optional skill read families without stacking exact-base overlays.

Independent request/source/global grants are retained for each family; a permit
for filesystem names does not grant ledger access or vice versa. No default app
wiring, source mutations, filesystem access or database reads during binding.
"""
from dataclasses import dataclass, field
import re
from .core import error_response
from .skill_usage_setup import bind_skill_usage_reads, _SkillUsageApplication
from .missing_skill_setup import bind_missing_skill_reads, _MissingSkillApplication
from .skill_files_setup import bind_skill_files_reads, _SkillFilesApplication

# Code-owned implementation anchors, captured when this module is loaded, not
# from potentially replaced methods at the time a caller asks to bind a view.
_VIEW_TYPES = (_SkillUsageApplication, _MissingSkillApplication, _SkillFilesApplication)
_VIEW_METHODS = tuple((cls.__call__, cls._check, cls.__init__) for cls in _VIEW_TYPES)
_BINDERS = (bind_skill_usage_reads, bind_missing_skill_reads, bind_skill_files_reads)


def _factory_integrity():
    if any(actual is not expected for actual,expected in zip(
            (bind_skill_usage_reads,bind_missing_skill_reads,bind_skill_files_reads),_BINDERS)):
        raise ValueError('Original skill read binders required')
    for cls,methods in zip(_VIEW_TYPES,_VIEW_METHODS):
        if any(actual is not expected for actual,expected in zip(
                (cls.__call__,cls._check,cls.__init__),methods)):
            raise ValueError('Original skill read implementations required')

_PATTERNS = (re.compile(r'/api/skills/[^/]+/usage'),
             re.compile(r'/api/skills/missing'),
             re.compile(r'/api/skills/[^/]+/files'))


def _key(method):
    return getattr(method,'__self__',None),getattr(method,'__func__',method)


def _same(method,key):
    return all(a is b for a,b in zip(_key(method),key))


@dataclass(frozen=True)
class SkillReadBundle:
    application: object = field(repr=False)
    usage: object = field(repr=False)
    missing: object = field(repr=False)
    files: object = field(repr=False)


class _SkillReadApplication:
    def __init__(self,original,views):
        _factory_integrity()
        for view,cls,methods in zip(views,_VIEW_TYPES,_VIEW_METHODS):
            if type(view) is not cls or view.original is not original or not _same(view.__call__,(view,methods[0])) or not _same(view._check,(view,methods[1])):
                raise ValueError('Exact original skill read view required')
        self.original=original
        self.usage_application,self.missing_application,self.files_application=views
        self._original=original
        self._views=tuple(views)
        self._types=_VIEW_TYPES
        self._methods=tuple(((view,methods[0]),(view,methods[1])) for view,methods in zip(views,_VIEW_METHODS))
        self._original_call=_key(original.__call__)

    def _check(self,index):
        if self.original is not self._original or not _same(self.original.__call__,self._original_call):
            raise PermissionError('Original skill application changed')
        view=(self.usage_application,self.missing_application,self.files_application)[index]
        if view is not self._views[index] or type(view) is not self._types[index] or view.original is not self._original or not _same(view.__call__,self._methods[index][0]) or not _same(view._check,self._methods[index][1]):
            raise PermissionError('Skill read family changed')
        view._check()

    def __call__(self,environ,start_response):
        path=environ.get('PATH_INFO','')
        index=next((i for i,pattern in enumerate(_PATTERNS) if type(path) is str and pattern.fullmatch(path)),None)
        if index is None:
            # Unrelated behavior stays with the original application, including
            # auth/status handling. The composition does not add another route.
            if self.original is not self._original or not _same(self.original.__call__,self._original_call):
                return self._unavailable(start_response)
            return self._original(environ,start_response)
        try:self._check(index)
        except Exception:return self._unavailable(start_response)
        body=self._views[index](environ,start_response)
        # A start_response callback can replace the selected family. Never hand
        # back the prepared body after a binding change, even if headers went out.
        try:self._check(index)
        except Exception:raise RuntimeError('Skill read composition changed after response start') from None
        return body

    @staticmethod
    def _unavailable(start_response):
        response=error_response(503,'Skill read composition unavailable')
        start_response('503 Service Unavailable',[('Content-Type','application/json'),
            ('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')])
        return [response.body]


def bind_skill_reads(application,*,usage_service,missing_service,files_service,
                     authorize_usage,authorize_missing,authorize_files):
    """Bind all three families explicitly; no partial mutation on failure.

    Each existing binder validates its own exact reader, scope, original JWT
    authority, callback and conflicting routes. All are constructed against the
    same untouched base, rather than weakening their exact-base requirements.
    """
    _factory_integrity()
    usage=bind_skill_usage_reads(application,usage_service=usage_service,
                                 authorize_global=authorize_usage)
    missing=bind_missing_skill_reads(application,missing_service=missing_service,
                                    authorize_global=authorize_missing)
    files=bind_skill_files_reads(application,files_service=files_service,
                                authorize_global=authorize_files)
    view=_SkillReadApplication(application,(usage.application,missing.application,files.application))
    for index in range(3):view._check(index)
    return SkillReadBundle(view,usage_service,missing_service,files_service)
