"""Independent read-only AST whitelist review; never execute fixture source."""
import unittest,tempfile
from pathlib import Path
import test_route_inventory as fixture

class MetadataScannerReview(unittest.TestCase):
    def scan(self,statement):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'python/artex/server';server.mkdir(parents=True)
            (server/'intercept_read_setup.py').write_text('class _InterceptReadApplication:\n    def __init__(self,services):\n        staged=Router(auth)\n'+statement)
            return fixture.audit.python_inventory(root)[0]
    def test_unknown_call_shapes_and_control_flow_rejected(self):
        cases=[
            "        if 'judge_usage' in services:staged.add('GET','/api/intercept/judge/usage',handler,public=False)\n",
            "        if 'judge_usage' in services:staged.add('GET','/api/intercept/judge/usage',handler,False)\n",
            "        if 'judge_usage' in services:staged.add('POST','/api/intercept/judge/usage',handler)\n",
            "        if 'judge_usage' in services:\n            staged.add('GET','/api/intercept/judge/usage',handler)\n            noop()\n",
            "        if 'judge_usage' in services == other:staged.add('GET','/api/intercept/judge/usage',handler)\n",
            "        if enabled:\n            if 'judge_usage' in services:staged.add('GET','/api/intercept/judge/usage',handler)\n",
        ]
        for source in cases:
            with self.subTest(source=source),self.assertRaises(fixture.audit.AuditError):self.scan(source)
    def test_duplicate_whitelisted_routes_still_rejected_by_grouping(self):
        statement="        if 'judge_usage' in services:staged.add('GET','/api/intercept/judge/usage',handler)\n"
        rows=self.scan(statement*2)
        self.assertEqual(len(rows),2)
        with self.assertRaises(fixture.audit.AuditError):fixture.audit.group_python_routes(rows)
    def test_all_four_exact_routes_remain_optional(self):
        pairs={'config':'/api/intercept/tool-config','rules':'/api/intercept/rules','judge_usage':'/api/intercept/judge/usage','notification_history':'/api/notify/deliveries'}
        rows=self.scan(''.join(f"        if {key!r} in services:staged.add('GET',{path!r},handler)\n" for key,path in pairs.items()))
        self.assertEqual({r['path'] for r in rows},set(pairs.values()))
        self.assertTrue(all(r['registration_mode']=='optional explicit service registration' for r in rows))

    def test_detail_does_not_expand_receiver_arity_or_branch_whitelist(self):
        cases=[
            "        other=Router(auth)\n        if 'detail' in services:other.add('GET','/api/intercept/history/{id}',handler)\n",
            "        if 'detail' in services:staged.add('GET','/api/intercept/history/{id}',handler,False)\n",
            "        if 'detail' in services:\n            staged.add('GET','/api/intercept/history/{id}',handler)\n        else:\n            staged.add('GET','/api/intercept/history/{id}',handler)\n",
            "        if 'detail' in services:\n            staged.add('GET','/api/intercept/history/{id}',handler)\n            noop()\n",
        ]
        for source in cases:
            with self.subTest(source=source),self.assertRaises(fixture.audit.AuditError):self.scan(source)
