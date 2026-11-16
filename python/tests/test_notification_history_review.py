import unittest
from unittest.mock import patch
from artex.server.core import json_response
import test_notification_history as fixture
class Review(unittest.TestCase):
 def test_raw_policy_revokes_read_on_final_guard_exit(self):
  from contextlib import contextmanager
  from artex.server.notification_history import NotificationHistoryReadService
  f=fixture.Tests();f.setUp();f.db.rows=[fixture.row()];late=[False]
  @contextmanager
  def guard():
   yield
   late[0]=True
  def raw(*a):
   if late[0]:f.allowed=False
   return True
  f.service=NotificationHistoryReadService(f.reader,authorize=lambda *a:f.allowed,raw_authorize=raw,current=lambda:True,lifecycle_guard=guard)
  r=f.get();self.assertEqual(r.status,403);self.assertNotIn(b'diagnostic',r.body)
 def test_raw_identity_replacement_during_serialization_not_invoked(self):
  f=fixture.Tests();f.setUp();f.db.rows=[fixture.row()];calls=[]
  def encode(*args):f.service.raw_authorize=lambda *a:calls.append(1) or True;return json_response(*args)
  with patch('artex.server.notification_history.json_response',side_effect=encode):r=f.get()
  self.assertEqual(r.status,500);self.assertFalse(calls)
 def test_mismatching_filtered_row_and_nonmonotonic_rows_rejected(self):
  f=fixture.Tests();f.setUp();f.db.rows=[fixture.row()]
  self.assertEqual(f.get('state=sent').status,500)
  f.db.rows=[fixture.row(),fixture.row()];self.assertEqual(f.get().status,500)
