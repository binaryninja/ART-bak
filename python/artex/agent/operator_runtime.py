"""Explicit operator inputs for construction of the existing inert assembly.

This boundary neither discovers configuration nor supplies authority. The caller
already owns the executor, provider resolver and transcript adapter, and chooses
every prompt, catalog, limit and callback. Activity and cleanup callbacks require
an explicit callback or None; timing/process policies are outside this boundary.

The input record is shallowly frozen. It does not freeze caller-owned resources
or metadata, establish lifecycle ownership, or protect against arbitrary mutation
inside the process. The existing assembly alone copies prompts/catalog metadata
and retains its original resource and policy checks. Custom metadata retains the
constructor's normal Python copy/serialization behavior.

The returned value is an actual TaskRuntimeAssembly, not an application, runner,
readiness receipt or execution grant. Construction checks identity and callable
presence without invoking host policies, selecting providers, reading inventory,
opening transcripts, or starting work. Catalog semantics and all operational
preconditions remain with their existing owners.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from ..chat_transcripts import ChatTranscriptAdapter
from ..db.core import Executor
from ..db.tool_catalog import CatalogTool
from .runtime import Grant, Limits
from .task_providers import TaskProviderResolver
from .task_runtime import TaskRuntimeAssembly


class OperatorExecutor(Executor, Protocol):
    """Caller-owned executor surface; composition does not call these methods."""

    def transaction(self) -> AbstractContextManager[Executor]: ...


@dataclass(frozen=True, kw_only=True, repr=False, eq=False)
class OperatorRuntimeInputs:
    """Required wiring, with no resource/prompt/callback representation in repr.

    No field has a default. Explicit None disables the optional activity or
    cleanup callback; omitting either choice is a construction error. Nested
    metadata is not snapshotted until compose_operator_runtime calls the original
    assembly constructor. Callback signatures vary by existing role/scope.
    """

    executor: OperatorExecutor
    providers: TaskProviderResolver
    transcript: ChatTranscriptAdapter
    prompts: Mapping[str, str]
    graph_catalog: Mapping[str, Sequence[CatalogTool]]
    limits: Limits
    policy_revision: str
    current: Callable[[int, int, str], bool]
    lifecycle_guard: Callable[..., AbstractContextManager[object]]
    authorize: Callable[..., Grant]
    asset_authorize: Callable[..., Grant]
    validate_input: Callable[..., bool]
    activity_authorize: Callable[..., Grant] | None
    cleanup_authorize: Callable[..., bool] | None


def compose_operator_runtime(inputs: OperatorRuntimeInputs) -> TaskRuntimeAssembly:
    """Construct one original assembly from explicit, already-owned resources.

    Validation errors propagate; no fallback wiring or cleanup is attempted.
    Existing provider resource pins and assembly bindings are checked without
    invoking their authority callbacks. These checks are not a readiness probe.
    """
    if type(inputs) is not OperatorRuntimeInputs:
        raise TypeError('Exact OperatorRuntimeInputs required')
    for name in ('fetch_all', 'fetch_one', 'execute', 'transaction'):
        if not callable(getattr(inputs.executor, name, None)):
            raise TypeError('Caller-owned transactional executor methods required')
    if type(inputs.providers) is not TaskProviderResolver:
        raise TypeError('Exact already-constructed TaskProviderResolver required')
    if type(inputs.transcript) is not ChatTranscriptAdapter:
        raise TypeError('Exact already-constructed ChatTranscriptAdapter required')
    if not isinstance(inputs.prompts, Mapping) or not isinstance(inputs.graph_catalog, Mapping):
        raise TypeError('Explicit prompt and graph catalog mappings required')
    for name in ('current', 'lifecycle_guard', 'authorize', 'asset_authorize', 'validate_input'):
        if not callable(getattr(inputs, name)):
            raise TypeError('Explicit host callbacks required')
    for name in ('activity_authorize', 'cleanup_authorize'):
        value = getattr(inputs, name)
        if value is not None and not callable(value):
            raise TypeError('Explicit optional callback or None required')

    provider_pins = inputs.providers._pins()
    inputs.providers._resources_check(provider_pins)
    assembly = TaskRuntimeAssembly(
        inputs.executor,
        providers=inputs.providers,
        transcript=inputs.transcript,
        prompts=inputs.prompts,
        graph_catalog=inputs.graph_catalog,
        limits=inputs.limits,
        policy_revision=inputs.policy_revision,
        current=inputs.current,
        lifecycle_guard=inputs.lifecycle_guard,
        authorize=inputs.authorize,
        asset_authorize=inputs.asset_authorize,
        validate_input=inputs.validate_input,
        activity_authorize=inputs.activity_authorize,
        cleanup_authorize=inputs.cleanup_authorize,
    )
    inputs.providers._resources_check(provider_pins)
    assembly.validate_bindings()
    return assembly
