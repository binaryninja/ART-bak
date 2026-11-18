# Configuration transaction coordination

A successful profile save has two observable stages. PostgreSQL commits the
change, then the process prepares and publishes its runtime snapshot. A client
can therefore see a committed profile while the host still reports incomplete
publication. Release of a transaction lock establishes no ordering between that
publication and another process's next commit.

The baseline for this assessment is `ffe629d49dee11baf123ea3eb804d6ac8d69813c`,
tree `9627a97ce8ad26835f3864a5dd9febeb65ef6442`. It has five transaction sites
in the committed coordinator and uses its original executor at each site.

## Opt-in configuration and lock order

`TransactionCoordinationBinding(executor, *, owner, database_name, domain, key)`
identifies the exact previously enrolled original PostgresExecutor or
ExecutorPool and its live ConfigurationOwner. The database and domain names
must be explicit, and the domain must match the owner. The signed 64-bit key
identifies the advisory lock in that database. Domain names do not partition
PostgreSQL keys. Each participant must agree on the database and key. A
database name check alone cannot authenticate the cluster.

`CommittedProfileCoordinator(..., transaction_coordination=binding)` checks
the exact binding type and executor identity, and verifies that both executor
and factory belong to the binding's owner. The default value `None` retains
the original transaction behavior. Neither construction nor use supplies an
owner grant. The coordinator retains the binding selected at construction,
including an explicit or default `None`. Its binding checks and each
transaction selection compare the current option to that original by identity.
Replacing the option with `None` or a different binding after opting in raises
`ProfileInvalid` at the next such check. ProfileHost also retains the selected
identity and checks it during host binding validation. These checks detect
replacement at those boundaries. They do not protect against arbitrary
privileged code modifying process memory.

The original host owns its gate, calls policy decisions and enters
the policy guard before the coordinator acquires its operation gate.

Inside each opted-in transaction, the order is as follows.

1. The coordinator selects the already-admitted operation and mode. Both save
   transactions use `profile.save` with write admission, and both default-switch
   transactions use `profile.set_active` with write admission. Reload uses
   `profile.reconcile` with read admission.
2. `coordinated_transaction(binding, *, operation, mode)` checks enrollment
   and the exact active operation and mode, then enters the original executor's
   fresh committed transaction. It checks admission again after acquisition.
3. On that transaction's backend it checks the database name and makes one
   nonwaiting advisory-lock attempt, shared for read or exclusive for write.
   A malformed returned row or mismatched database raises
   `TransactionCoordinationError`. Driver and fetch failures propagate.
   A conflicting lock raises `TransactionCoordinationBusy`. None of these
   conditions reaches the caller's business SQL.
4. After acquiring the lock it checks admission before yielding the original
   transaction resource. It checks admission again on normal return, before
   the original transaction exits. A successful try-lock places no upper
   bound on resource acquisition, driver calls, commit or rollback.
5. The transaction lock ends with its transaction. The caller's local owner
   guard can remain active across preparation or publication, but a competing
   process can acquire the database lock in that interval. An enclosing
   database transaction around ProfileHost would violate the coordinator's
   fresh-transaction requirement.

## Participants and remaining writers

| Path | Exclusion boundary | What can still race |
| --- | --- | --- |
| Committed coordinator, profile save and default switch | Each operation owns two separate transactions. The coordinator releases preflight before factory preparation, then checks the selected record again in the mutation transaction. | Another process may write between transactions or after mutation commit. Read-back covers the selected persisted state at that point. |
| Committed coordinator, explicit reload | One transaction captures active and pooled profiles. Publication follows transaction exit. | A later durable change may precede the local swap. Reload reflects the captured records. |
| Direct `ProfileStore.save` and `set_active` on an enrolled executor | The matching process-local owner operation is checked at entry and inside the store transaction. | Invoking the store directly does not enter a coordinator transaction. |
| `TaskStore.replace_llm_profiles_idle` and `ConversationStore.update_profile` | Matching local owner operation on the exact enrolled executor. The methods retain their original record checks. | Neither method enrolls its transaction in cross-process coordination. |
| Factory reads, preparation, cache invalidation and existing consumers | The enrolled factory checks local configuration access. The coordinator's publication lock protects a process-local swap. | A consumer can retain an earlier provider or snapshot. A database lock cannot revoke it. |
| Go writers, raw SQL, admin clients, migrations, provisioning, other executors and processes | Database privileges and the individual writer's own controls. | A writer with SQL privileges can omit an advisory lock, even on an otherwise enrolled connection. Task and conversation initialization, agent bindings, quota and retry-policy writers are outside the four named store hooks. |

The coverage inventory and operation names for local hooks are in
[Shared configuration ownership](SHARED_CONFIGURATION_OWNERSHIP.md).

## Failure and cancellation

| Observation | Interpretation |
| --- | --- |
| Refused before mutation starts | No mutation was attempted through that coordinator operation. Check the phase and original error. |
| Mutation started, successful transaction exit unconfirmed | `outcome_unknown` preserves uncertainty. An interrupted caller or a lost response cannot establish rollback. Replaying the write can duplicate a change. |
| Mutation transaction returned successfully, preparation/publication incomplete | `committed_pending` records confirmed persistence. Explicit reconciliation captures current stored configuration and can publish a fresh local view. |
| Applied | The coordinator published its captured view locally. Subsequent commits, including commits from another process, can make that view stale. |
| Cancellation requested or drain timed out | Ownership and resource cleanup follow actual driver settlement. A timeout, a signal and an attempted cancellation each leave the server outcome unresolved until there is separate evidence. |

## Task lifecycle evidence

The original manager issues the run and token. Its handle and generation checks
belong to that manager. A configuration capability, advisory-lock result or
matching task ID cannot establish lifecycle authority. Genuine shared lifecycle
integration remains missing, and the held original-state population paths have
no positive acceptance result here. An isolated PostgreSQL contention test can establish
exclusion among participating transactions under the tested database and key.
Any such result needs a separate execution receipt identifying the processes,
backends and observed transaction outcomes. A fixture barrier cannot establish
that an original manager admitted a task or that every lifecycle writer
respected its current generation.
