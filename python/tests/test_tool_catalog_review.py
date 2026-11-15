"""Independent opaque-metadata tests; no command execution or tool callbacks."""
from dataclasses import replace
import unittest
from artex.db.tool_catalog import CatalogTool, ToolCatalogStore, ToolInvalid
from artex.server.tool_catalog import ToolCatalogService
from artex.server.core import Request
import test_tool_catalog as fixture

class ToolCatalogReview(unittest.TestCase):
    def test_builtin_property_named_description_cannot_change_type(self):
        tool=CatalogTool('Read',True,'fixture',{'type':'object','properties':{'description':{'type':'string'}}},[],True,'builtin',{},False)
        row=tuple(getattr(tool,name) for name in tool.__dataclass_fields__)
        db=fixture.SQL([[row]])
        changed=replace(tool,schema={'type':'object','properties':{'description':{'type':'number'}}})
        with self.assertRaises(ToolInvalid):ToolCatalogStore(db).update(changed,custom=False)
        self.assertEqual(db.events,['begin','rollback'])
        self.assertEqual(len(db.calls),1)

    def test_unknown_binding_update_rolls_back_before_write(self):
        db=fixture.SQL([[fixture.ROW],[]])
        with self.assertRaises(ToolInvalid):ToolCatalogStore(db).update(CatalogTool(*fixture.ROW))
        self.assertEqual(db.events,['begin','rollback'])
        self.assertEqual(len(db.calls),2)
        self.assertTrue(db.calls[0].sql.endswith('FOR UPDATE'))
        self.assertTrue(db.calls[1].sql.endswith('FOR KEY SHARE'))

    def test_nested_duplicate_and_nonboolean_rejected_without_writes(self):
        db=fixture.SQL();service=ToolCatalogService(ToolCatalogStore(db))
        bodies=[b'{"key":"fixture","kind":"command","exec":{"command":"one","command":"two"}}',
                b'{"key":"fixture","kind":"command","enabled":1}',
                b'{"key":"fixture","kind":"command","deferred":null}']
        for body in bodies:
            result=service.handle('create',Request('POST','/api/tools/custom',{},body),{})
            self.assertEqual(result.status,400)
        self.assertEqual(db.calls,[])
