"""Explicit host-owned domain tools for the shared task engine."""
from dataclasses import dataclass
from typing import Callable
from ..db.tool_catalog import CatalogTool
from .runtime import Tool


@dataclass(frozen=True)
class DomainTools:
    revision: str
    catalog: tuple[CatalogTool, ...]
    bind: Callable

    def __post_init__(self):
        if not self.revision or not callable(self.bind) or type(self.catalog) is not tuple:
            raise ValueError('Explicit versioned domain tool bundle required')
        names = [row.key for row in self.catalog]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate domain tools')

    def tools(self, scope, current, guard):
        result = tuple(self.bind(scope, current, guard))
        if any(not isinstance(tool, Tool) for tool in result) or {t.name for t in result} != {r.key for r in self.catalog}:
            raise ValueError('Domain handlers must match their declared catalog')
        return result
