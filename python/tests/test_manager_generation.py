"""Installed-generation read capability; no providers, DB or filesystem effects."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import unittest
from artex.manager import TaskManager,CancellationToken
from artex.agent.host import AgentHost
from test_manager import Repository,Runner,Handle,record
class Lifecycle(Runner):
    def handles(self):return tuple(self.installed)
    def settle_completed(self,complete):return ()
    def __init__(self):super().__init__();del self.handles;self.installed=[]
    def start(self,row,token):
        self.tokens.append(token);result=Handle();self.installed.append(result);return result
class Tests(unittest.TestCase):
    def setUp(self):
        self.repo=Repository();self.runner=Lifecycle();self.manager=TaskManager(self.repo,runner=self.runner);self.manager.load_existing()
    def started(self):
        self.manager.start('1');return self.runner.tokens[-1],self.runner.installed[-1]
    def test_missing_and_preinstallation_never_owned(self):
        handle=Handle();token=CancellationToken()
        self.assertFalse(self.manager.owns_generation('1',token,handle))
        observed=[]
        def start(row,token):
            observed.extend([self.manager.owns_generation('1',token,handle),self.manager.owns_generation('1',token,handle,allow_cancelled=True)])
            return handle
        self.runner.start=start;self.manager.start('1')
        self.assertEqual(observed,[False,False]);self.assertTrue(self.manager.owns_generation('1',self.manager._runs['1'].token,handle))
    def test_exact_installed_identity_only(self):
        token,handle=self.started()
        self.assertTrue(self.manager.owns_generation(1,token,handle))
        for t,h in ((CancellationToken(),handle),(token,Handle()),(token,None)):
            self.assertFalse(self.manager.owns_generation('1',t,h));self.assertFalse(self.manager.owns_generation('1',t,h,allow_cancelled=True))
    def test_cancelled_cleanup_is_separate_not_execution(self):
        token,handle=self.started();token.cancel()
        self.assertFalse(self.manager.owns_generation('1',token,handle));self.assertTrue(self.manager.owns_generation('1',token,handle,allow_cancelled=True))
        self.manager._tasks['1'].update(paused=True,status='done')
        self.assertFalse(self.manager.owns_generation('1',token,handle));self.assertTrue(self.manager.owns_generation('1',token,handle,allow_cancelled=True))
    def test_retired_generation_and_replacement(self):
        old_token,old_handle=self.started();self.manager.pause('1')
        self.assertFalse(self.manager.owns_generation('1',old_token,old_handle,allow_cancelled=True))
        token,handle=self.started()
        self.assertTrue(self.manager.owns_generation('1',token,handle));self.assertFalse(self.manager.owns_generation('1',old_token,old_handle,allow_cancelled=True))
    def test_uncertain_deleted_archived_invalid_row_rejected_both_modes(self):
        token,handle=self.started()
        for patch_values in ({'deleted_at':'now'},{'archived_at':'now'},{'id':'2'},{'status':'unknown'},{'paused':None},{'queued':1}):
            row=self.manager._tasks['1'];old=row.copy();row.update(patch_values)
            for cleanup in (False,True):self.assertFalse(self.manager.owns_generation('1',token,handle,allow_cancelled=cleanup))
            row.clear();row.update(old)
        self.manager._runs['1'].uncertain=True
        self.assertFalse(self.manager.owns_generation('1',token,handle,allow_cancelled=True))
    def test_execution_requires_all_lifecycle_flags(self):
        token,handle=self.started()
        for changes in ({'paused':True},{'queued':True},{'status':'created'},{'status':'timeout'}):
            row=self.manager._tasks['1'];old=row.copy();row.update(changes)
            self.assertFalse(self.manager.owns_generation('1',token,handle));row.clear();row.update(old)
        self.manager._runs['1'].started=False
        self.assertFalse(self.manager.owns_generation('1',token,handle));self.assertTrue(self.manager.owns_generation('1',token,handle,allow_cancelled=True))
    def test_lookup_no_repository_or_handle_callbacks(self):
        token,handle=self.started()
        def fail(*a,**kw):raise AssertionError('callback invoked')
        with patch.object(self.repo,'get',fail),patch.object(self.repo,'list',fail),patch.object(handle,'wait',fail),patch.object(handle,'cancel',fail):
            self.assertTrue(self.manager.owns_generation('1',token,handle))
        for bad in (True,None,object(),'-1','a','0'):
            self.assertFalse(self.manager.owns_generation(bad,token,handle))
        self.assertFalse(self.manager.owns_generation('1',token,handle,allow_cancelled=1))
    def test_host_owned_advance_cannot_enter_before_handle_installed(self):
        host=AgentHost(self.manager,self.runner);entered,release=Event(),Event();observed=[];handle=Handle()
        def start(row,token):
            observed.append(self.manager.owns_generation('1',token,handle));self.runner.tokens.append(token)
            entered.set()
            if not release.wait(3):raise AssertionError('test gate timeout')
            self.runner.installed.append(handle);return handle
        self.runner.start=start;calls=[]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(host.start,'1')
            try:
                self.assertTrue(entered.wait(2));result=host.advance_owned(lambda:calls.append('ran'))
                self.assertTrue(result.busy);self.assertEqual(calls,[]);self.assertEqual(observed,[False])
            finally:release.set()
            self.assertTrue(future.result(2))
        result=host.advance_owned(lambda:self.manager.owns_generation('1',self.runner.tokens[-1],handle))
        self.assertFalse(result.busy);self.assertTrue(result.value)
