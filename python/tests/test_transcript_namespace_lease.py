"""Actual canonical store/session lease, inert filesystem/processes; no sockets."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.transcripts import TranscriptStore, TranscriptUnavailable, TranscriptError
from artex.transcript_checkpoint import inspect_checkpoint
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.agent.runtime import RunResult
from artex.server.runtime_listener import RuntimeListener, RuntimeResourceOwner
from artex.server.runtime_session import RuntimeSession
import test_runtime_listener as runtime_fixture


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'transcripts';self.root.mkdir(mode=0o700)
        self.store=TranscriptStore(self.root)
        self.lease=RuntimeLease.for_transcripts(self.store)
        self.addCleanup(self.lease.release)

    def can_lock(self,path=None):
        code='''import fcntl,sys
with open(sys.argv[1],'r+') as stream:
 try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:sys.exit(3)
'''
        result=subprocess.run([sys.executable,'-c',code,path or self.lease.path],capture_output=True,timeout=5)
        self.assertIn(result.returncode,(0,3),result.stderr)
        return result.returncode==0

    def test_factory_inert_one_shot_and_requires_acquisition_for_real_io(self):
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.lease.path,str(self.root/'.artex-runtime.lock'))
        with self.assertRaises(LeaseUnavailable):self.store.begin('chat')
        self.assertEqual(list(self.root.iterdir()),[])
        with self.assertRaises(TranscriptUnavailable):RuntimeLease.for_transcripts(self.store)
        self.lease.acquire();self.assertEqual(self.store.begin('chat').records,())
        self.assertTrue(self.lease.validate_runtime_store(self.store))
        self.lease.release()
        with self.assertRaises(LeaseUnavailable):self.store.read('chat')

    def test_single_binding_pointer_drift_never_downgrades_to_legacy_io(self):
        for field in ('_runtime_lease','_runtime_binding'):
            with self.subTest(field=field),patch.object(self.store,field,None):
                with self.assertRaises(TranscriptUnavailable):self.store.begin('bypassed')
                with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'bypassed')
                with self.assertRaises(TranscriptUnavailable):RuntimeLease.for_transcripts(self.store)
                with self.assertRaises(LeaseUnavailable):self.lease.acquire()
                self.assertEqual(list(self.root.iterdir()),[])
        self.lease.acquire();before=set(self.root.iterdir())
        for field in ('_runtime_lease','_runtime_binding'):
            with self.subTest(field=field),patch.object(self.store,field,object()):
                with self.assertRaises(TranscriptUnavailable):self.store.begin('bypassed')
                with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'bypassed')
                with self.assertRaises(LeaseUnavailable):self.lease.validate()
                self.assertFalse(self.can_lock())
        self.assertEqual(set(self.root.iterdir()),before)

    def test_original_owner_selection_rejects_instance_and_class_substitution(self):
        for acquired in (False,True):
            if acquired:self.lease.acquire()
            before=set(self.root.iterdir())
            for target in (self.store,TranscriptStore):
                with self.subTest(acquired=acquired,target=target),patch.object(target,'_runtime_owner',return_value=None) as substituted:
                    with self.assertRaises(LeaseUnavailable):self.store.begin('bypassed')
                    with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'bypassed')
                    with self.assertRaises(LeaseUnavailable):self.lease.acquire()
                    substituted.assert_not_called()
                    self.assertEqual(set(self.root.iterdir()),before)
            if acquired:self.assertFalse(self.can_lock())

    def test_checkpoint_requires_bound_acquisition_and_refuses_after_release(self):
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'chat')
        self.assertEqual(list(self.root.iterdir()),[])
        self.lease.acquire()
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'missing')
        self.assertFalse((self.root/'.lock-missing').exists())
        for agent in ('','worker'):
            self.store.record_message('chat',{'role':'user','content':[]},agent_id=agent)
            value=inspect_checkpoint(self.store,'chat',agent_id=agent)
            self.assertEqual(value.record_count,1);self.assertFalse(value.recovery_authorized)
        self.lease.release()
        with self.assertRaises(TranscriptError):inspect_checkpoint(self.store,'chat')
        self.assertEqual(self.lease._namespace_active,0)

    def test_checkpoint_first_legacy_io_refuses_late_binding_without_creating_files(self):
        store=TranscriptStore(self.root)
        with self.assertRaises(TranscriptError):inspect_checkpoint(store,'missing')
        self.assertEqual(list(self.root.iterdir()),[])
        with self.assertRaises(TranscriptUnavailable):RuntimeLease.for_transcripts(store)
        store.record_message('legacy',{'role':'user'})
        self.assertEqual(inspect_checkpoint(store,'legacy').record_count,1)

    def test_checkpoint_read_owns_lease_until_return(self):
        self.lease.acquire();self.store.record_message('chat',{'role':'user'})
        owner=object();self.lease.claim(owner);original=os.read;observed=[]
        inode=(self.root/'chat.jsonl').stat().st_ino
        def read(descriptor,size):
            if os.fstat(descriptor).st_ino==inode:
                self.assertFalse(self.lease._lock._is_owned())
                self.assertEqual(self.lease._namespace_active,1)
                with self.assertRaises(LeaseUnavailable):self.lease.release(owner=owner)
                with self.assertRaises(LeaseUnavailable):self.lease.transfer(owner,object())
                self.assertFalse(self.can_lock());observed.append(True)
            return original(descriptor,size)
        try:
            with patch('os.read',read):value=inspect_checkpoint(self.store,'chat')
            self.assertTrue(observed);self.assertEqual(value.record_count,1)
            self.assertEqual(self.lease._namespace_active,0)
            self.assertFalse(self.can_lock())
        finally:self.lease.release(owner=owner)

    def test_checkpoint_cleanup_tail_blocks_release_and_session_claim(self):
        self.lease.acquire();self.store.record_message('chat',{'role':'user'})
        original=os.close;inode=(self.root/'chat.jsonl').stat().st_ino
        entered,release=Event(),Event();errors=[];values=[]
        def close(descriptor):
            if os.fstat(descriptor).st_ino==inode:
                entered.set()
                if not release.wait(3):raise AssertionError('bounded checkpoint cleanup timeout')
            return original(descriptor)
        def work():
            try:values.append(inspect_checkpoint(self.store,'chat'))
            except BaseException as error:errors.append(error)
        with patch('os.close',close):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),errors)
                self.assertEqual(self.lease._namespace_active,1)
                with self.assertRaises(LeaseUnavailable):self.lease.release()
                with self.assertRaises(LeaseUnavailable):self.lease.claim(object())
                self.assertFalse(self.can_lock());self.assertEqual(values,[])
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(len(values),1);self.assertEqual(self.lease._namespace_active,0)

    def test_checkpoint_cleanup_drift_withholds_result_after_final_check(self):
        self.lease.acquire();self.store.record_message('chat',{'role':'user'})
        original=os.close;inode=(self.root/'chat.jsonl').stat().st_ino;old_path=self.lease.path
        def close(descriptor):
            changed=os.fstat(descriptor).st_ino==inode
            result=original(descriptor)
            if changed:self.lease.path=str(self.root/'substituted.lock')
            return result
        try:
            with patch('os.close',close):
                with self.assertRaisesRegex(TranscriptError,'^Transcript checkpoint unavailable$'):
                    inspect_checkpoint(self.store,'chat')
            self.assertEqual(self.lease._namespace_active,0)
            self.assertFalse(self.can_lock(old_path))
        finally:self.lease.path=old_path

    def test_checkpoint_baseexception_preserved_and_local_scope_released(self):
        self.lease.acquire();self.store.record_message('chat',{'role':'user'})
        original=os.read;inode=(self.root/'chat.jsonl').stat().st_ino;error=KeyboardInterrupt()
        def read(descriptor,size):
            if os.fstat(descriptor).st_ino==inode:
                self.assertEqual(self.lease._namespace_active,1)
                with self.assertRaises(LeaseUnavailable):self.lease.release()
                raise error
            return original(descriptor,size)
        with patch('os.read',read):
            with self.assertRaises(KeyboardInterrupt) as raised:inspect_checkpoint(self.store,'chat')
        self.assertIs(raised.exception,error);self.assertEqual(self.lease._namespace_active,0)
        self.assertFalse(self.can_lock());self.assertEqual(inspect_checkpoint(self.store,'chat').record_count,1)

    def test_binding_after_any_original_store_io_is_refused(self):
        old=TranscriptStore(self.root);old.read('legacy')
        with self.assertRaises(TranscriptUnavailable):RuntimeLease.for_transcripts(old)
        self.assertIsNone(old._runtime_lease)
        self.assertEqual(old.read('legacy'),[])

    def test_real_alias_root_contends_on_same_fixed_inode_without_unlink(self):
        self.lease.acquire();inode=os.stat(self.lease.path).st_ino
        other=RuntimeLease.for_transcripts(TranscriptStore(str(self.root)+'//'))
        with self.assertRaises(LeaseUnavailable):other.acquire()
        self.assertFalse(self.can_lock())
        self.lease.release();self.assertTrue(self.can_lock());other.acquire();other.release()
        self.assertEqual(os.stat(self.lease.path).st_ino,inode)

    def test_independent_native_process_uses_same_namespace_lock(self):
        code="import sys\nfrom artex.runtime_lease import RuntimeLease,LeaseUnavailable\nfrom artex.transcripts import TranscriptStore\nstore=TranscriptStore(sys.argv[1]);lease=RuntimeLease.for_transcripts(store)\ntry:lease.acquire()\nexcept LeaseUnavailable:sys.exit(3)\ntry:store.read('child')\nfinally:lease.release()\n"
        environment={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        self.lease.acquire()
        result=subprocess.run([sys.executable,'-c',code,str(self.root)],env=environment,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,3,result.stderr)
        self.assertFalse((self.root/'.lock-child').exists())
        self.lease.release()
        result=subprocess.run([sys.executable,'-c',code,str(self.root)],env=environment,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'.lock-child').exists())

    def test_untrusted_writable_ancestor_rejected_but_legacy_stays_unchanged(self):
        parent=Path(self.tmp.name)/'shared';parent.mkdir();parent.chmod(0o777)
        root=parent/'private';root.mkdir(mode=0o700)
        bound=RuntimeLease.for_transcripts(TranscriptStore(root))
        with self.assertRaises(LeaseUnavailable):bound.acquire()
        self.assertFalse((root/'.artex-runtime.lock').exists())
        self.assertEqual(TranscriptStore(root).read('legacy'),[])
        parent.chmod(0o700)
        self.assertTrue(bound.acquire());bound.release()

    def test_exact_store_identity_not_same_path_authority(self):
        self.lease.acquire()
        with self.assertRaises(LeaseUnavailable):self.lease.validate_runtime_store(TranscriptStore(self.root))
        class DerivedStore(TranscriptStore):pass
        class DerivedLease(RuntimeLease):pass
        with self.assertRaises(ValueError):RuntimeLease.for_transcripts(DerivedStore(self.root))
        with self.assertRaises(ValueError):DerivedLease.for_transcripts(TranscriptStore(self.root))

    def test_symlink_root_and_ancestor_never_create_target_lock(self):
        for ancestor in (False,True):
            with self.subTest(ancestor=ancestor),tempfile.TemporaryDirectory() as temp:
                base=Path(temp);target=base/'target';target.mkdir();nested=target/'inner';nested.mkdir()
                link=base/'link';link.symlink_to(target,target_is_directory=True)
                store=TranscriptStore(link/'inner' if ancestor else link)
                lease=RuntimeLease.for_transcripts(store)
                with self.assertRaises(LeaseUnavailable):lease.acquire()
                self.assertFalse((target/'.artex-runtime.lock').exists())
                self.assertFalse((nested/'.artex-runtime.lock').exists())

    def test_lock_symlink_hardlink_and_permissions_rejected_without_truncate(self):
        path=Path(self.lease.path);target=self.root/'preserve';target.write_text('unaltered');target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(LeaseUnavailable):self.lease.acquire()
        path.unlink();os.link(target,path)
        with self.assertRaises(LeaseUnavailable):self.lease.acquire()
        path.unlink();path.write_text('owned');path.chmod(0o644)
        with self.assertRaises(LeaseUnavailable):self.lease.acquire()
        self.assertEqual(target.read_text(),'unaltered');self.assertEqual(path.read_text(),'owned')
        path.chmod(0o600);self.lease.acquire();self.assertFalse(self.can_lock())

    def test_mutated_path_root_or_original_method_refuses_before_io(self):
        self.lease.acquire();before=set(self.root.iterdir())
        with patch.object(self.store,'root',str(self.root.parent)):
            with self.assertRaises(LeaseUnavailable):self.store.begin('never')
        with patch.object(self.lease,'path',str(self.root/'other.lock')):
            with self.assertRaises(LeaseUnavailable):self.store.begin('never')
        with patch.object(self.store,'_open_root_directory',side_effect=AssertionError('replaced')) as method:
            with self.assertRaises(LeaseUnavailable):self.store.begin('never')
            method.assert_not_called()
        self.assertEqual(set(self.root.iterdir()),before)
        unused=TranscriptStore(self.root);unused.root=str(self.root/'..')
        with self.assertRaises(ValueError):RuntimeLease.for_transcripts(unused)

    def test_renamed_root_or_replaced_lock_refuses_before_new_namespace_write(self):
        self.lease.acquire();old=self.root.with_name('old')
        self.root.rename(old);self.root.mkdir(mode=0o700)
        try:
            with self.assertRaises(LeaseUnavailable):self.store.begin('never')
            self.assertEqual(list(self.root.iterdir()),[])
            self.assertFalse(self.can_lock(str(old/'.artex-runtime.lock')))
        finally:
            self.root.rmdir();old.rename(self.root)
        lock=Path(self.lease.path);lock.rename(lock.with_name('old.lock'));lock.write_text('new');lock.chmod(0o600)
        with self.assertRaises(LeaseUnavailable):self.store.begin('never')
        self.assertFalse((self.root/'.lock-never').exists())
        self.lease.release()

    def test_pid_drift_refuses_io_and_does_not_unlock(self):
        self.lease.acquire()
        with patch('artex.runtime_lease.os.getpid',return_value=-1):
            with self.assertRaises(LeaseUnavailable):self.store.begin('never')
            with self.assertRaises(LeaseUnavailable):self.lease.release()
        self.assertFalse(self.can_lock());self.assertFalse((self.root/'.lock-never').exists())

    def test_real_scoped_transcript_commit_holds_lock_through_write_tail(self):
        self.lease.acquire();adapter=ChatTranscriptAdapter(self.store)
        entered,release=Event(),Event();errors=[];result=[]
        original=self.store._write
        def write(*a):
            value=original(*a);entered.set()
            if not release.wait(3):raise AssertionError('bounded fixture timeout')
            return value
        def work():
            try:result.append(adapter.begin_scoped('chat','client',{'role':'user','content':[{'type':'text','text':'inert'}]},{'task':5}))
            except BaseException as error:errors.append(error)
        with patch.object(self.store,'_write',write):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),errors)
                with self.assertRaises(LeaseUnavailable):self.lease.release()
                with self.assertRaises(LeaseUnavailable):self.lease.claim(object())
                self.assertFalse(self.can_lock());self.assertEqual(self.lease._namespace_active,1)
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors);self.assertEqual(len(result),1)
        self.assertEqual(self.lease._namespace_active,0);self.assertTrue(self.lease.release());self.assertTrue(self.can_lock())

    def test_guard_spans_native_cleanup_and_baseexception_preserves_original(self):
        self.lease.acquire();owner=object();self.lease.claim(owner)
        error=KeyboardInterrupt();original=self.store._load;observed=[]
        def load(*a):
            self.assertFalse(self.lease._lock._is_owned())
            with self.assertRaises(LeaseUnavailable):self.lease.release(owner=owner)
            with self.assertRaises(LeaseUnavailable):self.lease.transfer(owner,object())
            observed.append(True);raise error
        with patch.object(self.store,'_load',load):
            with self.assertRaises(KeyboardInterrupt) as raised:self.store.begin('chat')
        self.assertIs(raised.exception,error);self.assertEqual(observed,[True])
        self.assertEqual(self.lease._namespace_active,0);self.assertFalse(self.can_lock())
        self.lease.release(owner=owner)

    def test_native_directory_cleanup_tail_is_counted_without_holding_lease_mutex(self):
        self.lease.acquire();entered,release=Event(),Event();errors=[];tail=[False];blocked=[False]
        original_load=self.store._load;original_close=os.close;root_inode=self.root.stat().st_ino
        def load(*args):
            result=original_load(*args);tail[0]=True;return result
        def close(descriptor):
            if (tail[0] and not blocked[0] and descriptor != self.lease._directory
                    and os.fstat(descriptor).st_ino==root_inode):
                blocked[0]=True;entered.set()
                if not release.wait(3):raise AssertionError('bounded cleanup timeout')
            return original_close(descriptor)
        def work():
            try:self.store.begin('chat')
            except BaseException as error:errors.append(error)
        with patch.object(self.store,'_load',load),patch('os.close',close):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),errors)
                with self.assertRaises(LeaseUnavailable):self.lease.release()
                self.assertEqual(self.lease._namespace_active,1)
                self.assertFalse(self.can_lock())
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(self.lease._namespace_active,0)

    def test_binding_and_first_legacy_io_are_linearized(self):
        from threading import Barrier
        for _ in range(20):
            store=TranscriptStore(self.root);barrier=Barrier(2);outcomes=[]
            def read():
                barrier.wait(2)
                try:store.read('race');outcomes.append('read')
                except LeaseUnavailable:outcomes.append('refused')
            thread=Thread(target=read);thread.start();barrier.wait(2)
            try:
                owner=RuntimeLease.for_transcripts(store);bound=True
            except TranscriptUnavailable:bound=False
            thread.join(2);self.assertFalse(thread.is_alive())
            self.assertEqual(outcomes,['refused'] if bound else ['read'])

    def test_waiting_for_original_transcript_flock_also_retains_owner(self):
        self.lease.acquire();entered,release=Event(),Event();errors=[]
        import fcntl
        original=fcntl.flock
        def flock(fd,mode):
            entered.set()
            if not release.wait(3):raise AssertionError('bounded fixture timeout')
            return original(fd,mode)
        def work():
            try:self.store.begin('chat')
            except BaseException as error:errors.append(error)
        with patch('fcntl.flock',flock):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(LeaseUnavailable):self.lease.release()
                self.assertFalse(self.can_lock())
            finally:release.set();thread.join(3)
        self.assertFalse(errors);self.assertEqual(self.lease._namespace_active,0)

    def test_legacy_unbound_store_and_lease_remain_independent(self):
        legacy=TranscriptStore(self.root);lease=RuntimeLease(str(self.root/'legacy.lock'));lease.acquire()
        try:
            self.assertTrue(lease.validate_runtime_store(legacy))
            self.assertEqual(legacy.begin('legacy').records,())
            owner=object();permit=lease.claim(owner,guarded=True)
            with self.assertRaises(LeaseUnavailable):lease.release(owner=owner)
            self.assertTrue(lease.release(owner=owner,permit=permit))
            self.assertEqual(legacy.read('legacy'),[])
        finally:lease.release()


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.f=runtime_fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.bundle.runtime.transcript.store
        self.lease=RuntimeLease.for_transcripts(self.store);self.lease.acquire();self.addCleanup(self.lease.release)
        self.listener=RuntimeListener(self.f.bundle,self.f.owner)

    def test_actual_runtime_session_pairs_bound_store_and_closes_after_drain(self):
        session=RuntimeSession(self.listener,self.lease);self.addCleanup(session.stop,1)
        self.store.begin('inert')
        self.assertTrue(session.stop(1).stopped)
        with self.assertRaises(LeaseUnavailable):self.store.begin('after')

    def test_actual_session_stop_retains_bound_io_outside_host_then_explicit_retry(self):
        session=RuntimeSession(self.listener,self.lease);self.addCleanup(session.stop,1)
        entered,release=Event(),Event();errors=[];original=self.store._load
        def load(*args):
            entered.set()
            if not release.wait(3):raise AssertionError('bounded I/O timeout')
            return original(*args)
        def work():
            try:self.store.read('owned-io')
            except BaseException as error:errors.append(error)
        with patch.object(self.store,'_load',load):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),errors)
                self.assertFalse(session.stop(0).stopped)
                self.assertTrue(self.listener._closed)
                self.assertTrue(self.lease.validate())
                self.assertEqual(self.lease._namespace_active,1)
            finally:release.set();thread.join(3)
        self.assertFalse(errors);self.assertFalse(thread.is_alive())
        self.assertTrue(session.stop(1).stopped)

    def test_actual_session_stop_retains_checkpoint_read_then_explicit_retry(self):
        session=RuntimeSession(self.listener,self.lease);self.addCleanup(session.stop,1)
        self.store.record_message('checkpoint',{'role':'user'})
        entered,release=Event(),Event();errors=[];values=[];original=os.read
        inode=(Path(self.store.root)/'checkpoint.jsonl').stat().st_ino
        def read(descriptor,size):
            if os.fstat(descriptor).st_ino==inode:
                entered.set()
                if not release.wait(3):raise AssertionError('bounded checkpoint I/O timeout')
            return original(descriptor,size)
        def work():
            try:values.append(inspect_checkpoint(self.store,'checkpoint'))
            except BaseException as error:errors.append(error)
        with patch('os.read',read):
            thread=Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),errors)
                self.assertFalse(session.stop(0).stopped)
                self.assertTrue(self.listener._closed)
                self.assertTrue(self.lease.validate())
                self.assertEqual(self.lease._namespace_active,1)
            finally:release.set();thread.join(3)
        self.assertFalse(errors);self.assertFalse(thread.is_alive())
        self.assertEqual(len(values),1);self.assertEqual(values[0].record_count,1)
        self.assertTrue(session.stop(1).stopped)

    def test_session_refuses_other_store_even_same_directory_before_claim(self):
        wrong=RuntimeLease.for_transcripts(TranscriptStore(self.store.root))
        # Release original namespace lease; the other process-style owner can
        # acquire, but same path is not authority over this runtime's store.
        self.lease.release();wrong.acquire();self.addCleanup(wrong.release)
        with self.assertRaises(LeaseUnavailable):RuntimeSession(self.listener,wrong)
        self.assertIsNone(wrong._holder)
        self.assertEqual(self.f.calls,[])


if __name__=='__main__':unittest.main()
