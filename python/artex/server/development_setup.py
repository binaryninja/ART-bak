"""Strict tuning-to-development assembly with separately supplied host authority.

Acquire the lease before creating the runtime/resources passed here. JSON contains
only bounded tuning, never credentials, Python factories, policies or grants.
No listener or pump starts here. Failures retain ownership for explicit cleanup.
"""
from dataclasses import dataclass, field
from ..runtime_config_check import parse_runtime_tuning
from ..runtime_lease import RuntimeLease
from ..agent.task_runtime import TaskRuntimeAssembly
from ..db.tasks import TaskStore
from ..db.pool import ExecutorPool
from .task_read_builder import TaskReadBuilder
from .task_runtime_read_router import TaskRuntimeReadRouter
from .task_runtime_setup import build_task_runtime_application
from .runtime_listener import RuntimeResourceOwner, RuntimeListener, DevListenerConfig
from .runtime_session import RuntimeSession


@dataclass
class DevelopmentAssembly:
    """Retained references, including partial construction on failure."""
    runtime: object = field(repr=False)
    owner: object = field(repr=False)
    lease: object = field(repr=False)
    tuning: object = field(repr=False)
    reads: object = field(default=None, repr=False)
    router: object = field(default=None, repr=False)
    application: object = field(default=None, repr=False)
    listener: object = field(default=None, repr=False)
    session: object = field(default=None, repr=False)


class DevelopmentAssemblyFailed(RuntimeError):
    def __init__(self, assembly):
        super().__init__('Development assembly failed; retain resources and lease for explicit cleanup')
        self.assembly = assembly


def prepare_development_runtime(runtime, owner, lease, tuning_bytes, *,
        signing_key, password_codec, live_last_activity, profile_available,
        read_revision, authorize_selector, read_current, read_guard, read_policies,
        single_process, capacity=1, max_pending=64):
    """Construct the real task/read/listener/session stack without starting it.

    read_policies is code-owned TaskReadBuilder keyword arguments, not JSON.
    Explicit host callbacks retain their original semantics. Capacity/ownership
    declarations are host inputs, not inferred from valid syntax or a lock file.
    No timed/manual tools, optional catalogs or execution capability is invented.
    """
    tuning = parse_runtime_tuning(tuning_bytes)
    if type(runtime) is not TaskRuntimeAssembly or type(owner) is not RuntimeResourceOwner or type(lease) is not RuntimeLease:
        raise ValueError('Exact runtime, resource owner and acquired lease required')
    if type(owner.pool) is not ExecutorPool or runtime.executor is not owner.pool or single_process is not True:
        raise ValueError('Explicit shared-pool single-process ownership required')
    if type(capacity) is not int or type(max_pending) is not int or not 1 <= capacity <= max_pending <= 10000:
        raise ValueError('Bounded explicit admission capacity required')
    if type(read_policies) is not dict or any(key in read_policies for key in ('executor','max_rows','max_bytes')):
        raise ValueError('Read policies cannot override configured storage or bounds')
    if any(not callable(f) for f in (live_last_activity, profile_available, authorize_selector, read_current, read_guard)):
        raise ValueError('Explicit runtime and independent read authority required')
    if any(not callable(f) for f in (owner.stop_admissions,owner.cancel_owned,owner.close_resources)):
        raise ValueError('Explicit resource shutdown ownership required')
    lease.validate()
    assembly = DevelopmentAssembly(runtime,owner,lease,tuning)
    lease.claim(assembly)
    try:
        assembly.reads = TaskReadBuilder(owner.pool, max_rows=tuning.read_max_rows,
            max_bytes=tuning.read_max_bytes, **dict(read_policies))
        assembly.router = TaskRuntimeReadRouter(TaskStore(owner.pool),assembly.reads,
            revision=read_revision,authorize_selector=authorize_selector,
            current=read_current,lifecycle_guard=read_guard)
        lease.validate()
        assembly.application = build_task_runtime_application(runtime,signing_key,password_codec,
            live_last_activity=live_last_activity,profile_available=profile_available,
            capacity=capacity,max_pending=max_pending,single_process=single_process,
            pump_interval=tuning.pump_interval,pump_max_per_tick=tuning.pump_max_per_tick,
            read_router=assembly.router)
        lease.validate()
        assembly.listener = RuntimeListener(assembly.application,owner,
            DevListenerConfig(tuning.port,tuning.max_connections,tuning.socket_timeout))
        assembly.session = RuntimeSession(assembly.listener,lease,lease_owner=assembly)
        return assembly
    except BaseException:
        # No implicit rollback: construction may have consulted caller-owned
        # SQL or policies. Never claim their effects were undone or release the
        # process lease while cleanup ownership is uncertain.
        raise DevelopmentAssemblyFailed(assembly) from None
