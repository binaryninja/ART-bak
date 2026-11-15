import json
import unittest
from artex.db.tool_catalog import *
from artex.db.postgres import NoRowsError
from test_agents import SQL
ROW=('fixture',False,'Fixture',{'type':'object'},['custom'],True,'command',{'command':'inert fixture'},False)

class ToolStoreTests(unittest.TestCase):
    def test_reads_json_objects_and_usage(self):
        db=SQL([[ROW],[ROW],[('fixture',7)]]);store=ToolCatalogStore(db)
        self.assertEqual(db.calls,[])
        self.assertEqual(store.list()[0].to_dict()['exec'],{'command':'inert fixture'})
        self.assertEqual(store.get('fixture').agents,['custom']);self.assertEqual(store.usage_counts(),{'fixture':7})
    def test_create_bound_data_and_agent_lock(self):
        db=SQL([[(1,)],('fixture',)])
        ToolCatalogStore(db).create(CatalogTool(*ROW))
        self.assertEqual(db.events,['begin','commit']);self.assertIn('FOR KEY SHARE',db.calls[0].sql)
        self.assertNotIn('inert fixture',db.calls[1].sql)
        self.assertEqual(json.loads(db.calls[1].params[6]),ROW[7])
    def test_unknown_binding_no_insert(self):
        db=SQL([[]])
        with self.assertRaises(ToolInvalid):ToolCatalogStore(db).create(CatalogTool(*ROW))
        self.assertEqual(len(db.calls),1);self.assertEqual(db.events,['begin','rollback'])
    def test_no_rows_duplicate_and_other_error(self):
        for value,error in ((None,ToolConflict),(NoRowsError(),ToolConflict),(RuntimeError(),RuntimeError)):
            db=SQL([[(1,)],value])
            with self.assertRaises(error):ToolCatalogStore(db).create(CatalogTool(*ROW))
            self.assertEqual(db.events,['begin','rollback'])
    def test_update_and_missing(self):
        db=SQL([[ROW],[(1,)],1]);ToolCatalogStore(db).update(CatalogTool(*ROW))
        self.assertIn('FOR UPDATE',db.calls[0].sql);self.assertIn('system=false',db.calls[-1].sql)
        db=SQL([[]])
        with self.assertRaises(ToolMissing):ToolCatalogStore(db).update(CatalogTool(*ROW))
    def test_builtin_structure_only_annotations(self):
        from dataclasses import replace
        base=CatalogTool('Read',True,'Read',{'type':'object','properties':{'file':{'type':'string'}}},[],True,'builtin',{},False)
        row=tuple(getattr(base,n) for n in base.__dataclass_fields__)
        changed=replace(base,schema={'type':'object','properties':{'file':{'type':'string','description':'new'}}})
        db=SQL([[row],1]);ToolCatalogStore(db).update(changed,custom=False)
        db=SQL([[row]])
        with self.assertRaises(ToolInvalid):ToolCatalogStore(db).update(replace(base,schema={}),custom=False)
        self.assertEqual(len(db.calls),1)
    def test_custom_delete_and_builtin_protection(self):
        db=SQL([[ROW],1]);ToolCatalogStore(db).delete('fixture');self.assertEqual(db.events,['begin','commit'])
        row=list(ROW);row[1]=True;db=SQL([[tuple(row)]])
        with self.assertRaises(ToolInvalid):ToolCatalogStore(db).delete('fixture')
        self.assertEqual(len(db.calls),1)
    def test_validation_no_execution(self):
        from dataclasses import replace
        base=CatalogTool(*ROW)
        for bad in (replace(base,agents=[{}]),replace(base,enabled=1),replace(base,kind='http'),replace(base,exec=[]),replace(base,agents=['unknown-key'])):
            db=SQL()
            with self.assertRaises(ToolInvalid):ToolCatalogStore(db).create(bad)
            self.assertEqual(db.calls,[])
