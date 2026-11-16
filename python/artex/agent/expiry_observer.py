"""Explicit manual observation of one original owned clock; no model execution.

Call independently of the engine pump. A conservative local expiry may freeze
execution before DB expiry; clean drain then waits normally, without allocating
an assessment. Construction never stamps, reads SQL, starts threads or adopts
another task. Positive runtime entry remains disabled.
"""
from dataclasses import dataclass
from threading import Lock
from .deadline import OwnedGenerationClock
from .host import AgentHost

class ExpiryUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class ExpiryObservation:
    phase:str
    database_expired:bool=False
    drained:bool=False

class OwnedExpiryObserver:
    def __init__(self,host,generation_clock,*,authorize_observation,authorize_freeze):
        if type(host) is not AgentHost or type(generation_clock) is not OwnedGenerationClock:
            raise ValueError('Actual host and shared clock required')
        store=generation_clock.store
        if host.manager is not store.manager or host.runner is not store.manager.runner:
            raise ValueError('Same original host required')
        if not callable(authorize_observation) or not callable(authorize_freeze):
            raise ValueError('Explicit observation and freeze grants required')
        self.host=host;self.clock=generation_clock
        self.authorize_observation=authorize_observation;self.authorize_freeze=authorize_freeze
        self._pins=(host,generation_clock,store,store.manager,store.handle,store.handle.token,
                    authorize_observation,authorize_freeze)
        self._lock=Lock();self._lease=None;self._freeze=None;self._failed=False
    def _check(self):
        h,k,s,m,handle,token,read,freeze=self._pins
        if (self.host is not h or self.clock is not k or k.store is not s or s.manager is not m
            or s.handle is not handle or handle.token is not token or h.manager is not m
            or h.runner is not m.runner or self.authorize_observation is not read
            or self.authorize_freeze is not freeze):raise ExpiryUnavailable('Expiry binding changed')
        k._check()
        if self._lease is not None:
            m.validate_deadline_observation(str(s.receipt.task_id),token,handle,self._lease,self._freeze)
        if self._failed or k.unresolved:raise ExpiryUnavailable('Expiry ownership requires reconciliation')
    def _allow(self,callback):
        self._check()
        if callback(self._pins[2].receipt) is not True:raise ExpiryUnavailable('Expiry observation denied')
        self._check()
    def tick(self):
        if not self._lock.acquire(False):return ExpiryObservation('busy')
        try:
            self._check();snapshot=self.clock.observe()
            if snapshot is None:return ExpiryObservation('busy')
            if snapshot.lease is None:return ExpiryObservation('unstamped')
            if self._lease is None:self._lease=snapshot.lease
            elif self._lease is not snapshot.lease:raise ExpiryUnavailable('Expiry lease changed')
            self._allow(self._pins[6])
            store=self._pins[2];handle=self._pins[4];token=self._pins[5]
            expired=store.observe_expiry(self._lease,token=token,handle=handle,freeze=self._freeze)
            self._allow(self._pins[6])
            if self._freeze is None and not (snapshot.expired or expired):return ExpiryObservation('observing')
            self._allow(self._pins[7])
            receipt,drained=self.host.freeze_deadline(str(store.receipt.task_id),token,handle,self._lease,timeout=0.0)
            if self._freeze is not None and receipt is not self._freeze:raise ExpiryUnavailable('Freeze identity changed')
            self._freeze=receipt
            self._allow(self._pins[7]);self._allow(self._pins[6])
            # Re-read after drain/policy callbacks. DB false is ordinary waiting.
            expired=store.observe_expiry(self._lease,token=token,handle=handle,freeze=receipt)
            self._allow(self._pins[6]);self._allow(self._pins[7]);self._check()
            return ExpiryObservation('ready' if drained and expired else 'waiting_database' if drained else 'draining',expired,drained)
        except BaseException:
            self._failed=True
            if self._freeze is not None:
                try:self._pins[3].quarantine_deadline_freeze(self._freeze)
                except BaseException:pass
            raise
        finally:self._lock.release()
