import unittest
from unittest.mock import patch
from artex.server.core import json_response
from artex.db.judge_usage_read import UsageUnavailable
import test_judge_usage_read as fixture
class Review(unittest.TestCase):
 def test_exact_large_integer_and_inconsistent_totals(self):
  f=fixture.Tests();f.setUp();n=2**53+1
  f.db.rows=[(2,n,0,0,0,'2026-01-01',1,n,0)]
  self.assertEqual(f.get().json()['input_tokens'],n)
  f.db.rows.append((3,n,0,0,0,'2026-01-02',1,0,0))
  self.assertEqual(f.get().status,500)
 def test_final_serialization_callback_identity_swap(self):
  f=fixture.Tests();f.setUp();called=[]
  def encode(*args):
   f.service.authorize=lambda *a:called.append(1) or True
   return json_response(*args)
  with patch('artex.server.judge_usage_read.json_response',side_effect=encode):r=f.get()
  self.assertEqual(r.status,500);self.assertFalse(called);self.assertNotIn(b'input_tokens',r.body)
