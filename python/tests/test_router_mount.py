import unittest
from artex.server.core import Router,Request,json_response
class RouterMountTests(unittest.TestCase):
    def make(self):
        stage=Router(lambda request:False)
        stage.add('GET','/item/{id}',lambda r,p:json_response(200,{'id':p['id']}))
        return stage
    def test_explicit_parent_policy_is_enforced(self):
        parent=Router(lambda request:request.header('Authorization')=='fixture')
        with self.assertRaises(ValueError):parent.mount_protected(self.make())
        parent.mount_protected(self.make(),use_parent_authorizer=True)
        self.assertEqual(parent.dispatch(Request('GET','/item/1')).status,401)
        self.assertEqual(parent.dispatch(Request('GET','/item/1',{'Authorization':'fixture'})).json(),{'id':'1'})
    def test_renamed_duplicate_has_no_partial_mount(self):
        parent=Router(lambda request:True);parent.add('GET','/item/{other}',lambda *a:None)
        stage=self.make();stage.add('GET','/safe',lambda *a:None)
        before=list(parent._routes)
        with self.assertRaises(ValueError):parent.mount_protected(stage,use_parent_authorizer=True)
        self.assertEqual(parent._routes,before)
    def test_internal_shape_duplicate_rejected_atomically(self):
        parent=Router(lambda request:True);stage=self.make();stage.add('GET','/item/{other}',lambda *a:None)
        with self.assertRaises(ValueError):parent.mount_protected(stage,use_parent_authorizer=True)
        self.assertEqual(parent._routes,[])
    def test_public_or_self_staging_rejected(self):
        parent=Router(lambda request:True);stage=self.make();stage.add('GET','/public',lambda *a:None,public=True)
        with self.assertRaises(ValueError):parent.mount_protected(stage,use_parent_authorizer=True)
        with self.assertRaises(ValueError):parent.mount_protected(parent,use_parent_authorizer=True)
        self.assertEqual(parent._routes,[])
if __name__=='__main__':unittest.main()
