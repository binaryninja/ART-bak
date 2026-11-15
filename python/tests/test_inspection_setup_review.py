"""Independent inert assembly regressions; no database or transports."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from artex.server.inspection_setup import build_inspection_application, AuthorizedToolCatalogService
from artex.server.core import Router, Request
from artex.db.tool_catalog import ToolCatalogStore


class InspectionReview(unittest.TestCase):
    def test_parameter_renamed_duplicate_drains_before_any_mount(self):
        executor = object()
        router = Router(lambda req: True)
        router.add('GET', '/api/llm/records/{other}', lambda *args: None)
        runtime = SimpleNamespace(application=SimpleNamespace(router=router), close=Mock())
        before = list(router._routes)
        with patch('artex.server.inspection_setup.build_chat_application', return_value=runtime):
            with self.assertRaisesRegex(ValueError, 'already registered'):
                build_inspection_application(executor, b'x'*32, object(), chat_options={},
                    record_store_resolver=lambda *args: None, record_authorize=lambda *args: True,
                    record_redact=lambda *args: {}, tool_store=ToolCatalogStore(executor),
                    tool_authorize=lambda *args: True)
        self.assertEqual(router._routes, before)
        runtime.close.assert_called_once_with(cancel=True)

    def test_policy_requires_literal_true_before_store_read(self):
        store = ToolCatalogStore(object())
        store.list = Mock(side_effect=AssertionError('Must not read catalog'))
        for permission in (1, 'true', None):
            service = AuthorizedToolCatalogService(store, lambda *args: permission)
            response = service.handle('list', None, {})
            self.assertEqual(response.status, 403)
        store.list.assert_not_called()
