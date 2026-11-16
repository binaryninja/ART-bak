"""Independent metadata disclosure and PostgreSQL timezone representation checks."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import contextmanager,nullcontext
from unittest.mock import patch
from artex.server.core import Request,json_response
from artex.server.tool_intercept_read import ToolInterceptReadService
from artex.db.tool_intercept_read import ToolInterceptReader
import test_tool_intercept_read as fixture

class Review(unittest.TestCase):
 def setup_service(self):
  db=fixture.DB();db.rows=[fixture.row()];allowed=[True]
  service=ToolInterceptReadService(ToolInterceptReader(db),authorize=lambda r:allowed[0],current=lambda:True,lifecycle_guard=lambda:nullcontext())
  return db,allowed,service,Request('GET','/api/intercept/rules')
 def test_zoneinfo_timestamp_from_postgres_is_valid(self):
  db,allowed,s,r=self.setup_service();row=list(db.rows[0]);row[12]=row[13]=datetime(2026,1,2,3,4,5,123000,tzinfo=ZoneInfo('UTC'));db.rows=[tuple(row)]
  result=s.handle(r);self.assertEqual(result.status,200);self.assertEqual(result.json()['rules'][0]['created_at'],'2026-01-02T03:04:05.123Z')
 def test_final_authorizer_swap_never_invoked(self):
  db,allowed,s,r=self.setup_service();called=[]
  def encode(*a):s.authorize=lambda r:called.append(1) or True;return json_response(*a)
  with patch('artex.server.tool_intercept_read.json_response',side_effect=encode):result=s.handle(r)
  self.assertEqual(result.status,500);self.assertFalse(called);self.assertNotIn(b'private policy',result.body)
 def test_guard_exit_current_revocation_hides_rule(self):
  db,allowed,s,r=self.setup_service()
  @contextmanager
  def guard():
   yield
   allowed[0]=False
  service=ToolInterceptReadService(s.reader,authorize=lambda r:True,current=lambda:allowed[0],lifecycle_guard=guard)
  result=service.handle(r);self.assertEqual(result.status,403);self.assertNotIn(b'private policy',result.body)
