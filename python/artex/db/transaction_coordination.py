"""Serialize cooperating transactions on the same PostgreSQL database and key.

The host supplies the verified connection and agrees on one key per domain
across all participants. A database name check cannot authenticate a cluster.
The advisory lock lasts through the original driver's transaction exit only.
"""
from contextlib import contextmanager
from dataclasses import KW_ONLY, dataclass

from ..configuration_owner import ConfigurationDenied, ConfigurationOwner, _stack
from .configuration_ownership import assert_bound
from .core import Query, int64
from .pool import ExecutorPool
from .postgres import PostgresExecutor


class TransactionCoordinationError(RuntimeError):
    """The transaction's database or lock response failed validation."""


class TransactionCoordinationBusy(TransactionCoordinationError):
    """Another transaction held a conflicting lock at the time of the attempt."""


@dataclass(frozen=True, eq=False)
class TransactionCoordinationBinding:
    executor: PostgresExecutor | ExecutorPool
    _: KW_ONLY
    owner: ConfigurationOwner
    database_name: str
    domain: str
    key: int

    def __post_init__(self):
        if type(self.executor) not in (PostgresExecutor, ExecutorPool):
            raise TypeError('Original executor or pool required')
        assert_bound(self.executor, self.owner)
        for name in (self.database_name, self.domain):
            if type(name) is not str or not name.strip() or '\0' in name:
                raise ValueError('Explicit database and domain names required')
        if self.domain != self.owner.domain:
            raise ValueError('Coordination domain must match the enrolled owner')
        int64(self.key, 'Advisory lock key')


_DATABASE = Query('SELECT pg_catalog.current_database()')
_LOCK_SQL = {
    'read': 'SELECT pg_catalog.pg_try_advisory_xact_lock_shared($1::pg_catalog.int8)',
    'write': 'SELECT pg_catalog.pg_try_advisory_xact_lock($1::pg_catalog.int8)',
}


def _require_admission(binding, operation, mode):
    assert_bound(binding.executor, binding.owner)
    binding.owner._require(operation, mode)
    # A store read may use a read grant under a different active operation.
    # Choosing the database lock strength requires the actual admitted mode.
    lease = _stack()[-1]
    if lease.operation != operation or lease.mode != mode:
        raise ConfigurationDenied('Matching admitted coordination operation and mode required')


def _scalar(executor, query, expected_type):
    row = executor.fetch_one(query)
    if type(row) is not tuple or len(row) != 1 or type(row[0]) is not expected_type:
        raise TransactionCoordinationError('Unexpected PostgreSQL coordination result')
    return row[0]


@contextmanager
def coordinated_transaction(binding, *, operation, mode):
    """Enter a fresh committed transaction under an existing owner admission."""
    if type(binding) is not TransactionCoordinationBinding:
        raise TypeError('Exact transaction coordination binding required')
    _require_admission(binding, operation, mode)
    with binding.executor.committed_transaction() as executor:
        _require_admission(binding, operation, mode)
        if _scalar(executor, _DATABASE, str) != binding.database_name:
            raise TransactionCoordinationError('Unexpected PostgreSQL database')
        if not _scalar(executor, Query(_LOCK_SQL[mode], (binding.key,)), bool):
            raise TransactionCoordinationBusy('Conflicting PostgreSQL transaction lock')
        _require_admission(binding, operation, mode)
        yield executor
        _require_admission(binding, operation, mode)
