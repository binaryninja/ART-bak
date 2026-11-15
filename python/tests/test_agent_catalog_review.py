"""Independent catalog regressions using inert transaction-shaped SQL."""
import unittest
from artex.db.agents import AgentStore, AgentConflict, AgentUnavailable
from artex.db.postgres import NoRowsError
from artex.server.agents import AgentService
from artex.server.core import Request
import test_agents as fixture


class CatalogReview(unittest.TestCase):
    def test_real_driver_duplicate_rollback_becomes_conflict(self):
        db=fixture.SQL([NoRowsError('query returned no rows')])
        service=AgentService(AgentStore(db))
        response=service.handle('create',Request('POST','/api/agents',{},b'{"key":"custom","name":"Custom"}'),{})
        self.assertEqual(response.status,409)
        self.assertEqual(db.events,['begin','rollback'])
        self.assertEqual(len(db.calls),1)

    def test_prompt_activation_failure_rolls_back_agent_and_prompt(self):
        db=fixture.SQL([fixture.ROW,(7,),0])
        with self.assertRaises(AgentUnavailable): AgentStore(db).create('custom','Custom')
        self.assertEqual(db.events,['begin','rollback'])
        self.assertEqual(db.calls[2].params,(7,1))

    def test_missing_catalog_key_does_not_use_fetch_one(self):
        db=fixture.SQL([[]])
        self.assertIsNone(AgentStore(db).get('missing'))
        self.assertEqual(db.calls[0].params,('missing',))
