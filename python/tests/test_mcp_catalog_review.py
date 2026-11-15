"""Independent metadata confidentiality regressions; fake storage only."""
import unittest
import test_mcp_catalog as fixtures

class Review(unittest.TestCase):
    make=fixtures.MCPTests.make
    call=fixtures.MCPTests.call
    def test_cached_metadata_cannot_echo_credentials_embedded_in_config(self):
        row=(7,'Fixture','http','',['--token=arg-credential'],{'Authorization':'Bearer header-credential'},'https://user:password-credential@example.test/mcp?key=url-credential',True,False)
        self.make([[row],[('tool','arg-credential header-credential password-credential url-credential')]])
        result=self.call('tools');self.assertEqual(result.status,200,result.body)
        for value in ('arg-credential','header-credential','password-credential','url-credential'):
            self.assertNotIn(value.encode(),result.body)
    def test_environment_header_keys_are_opaque_private_config(self):
        row=(7,'Fixture','http','',[],{'header-key-credential':'value-credential'},'',True,False)
        self.make([[row],[]]);result=self.call('list');self.assertEqual(result.status,200)
        self.assertNotIn(b'header-key-credential',result.body)
    def test_redacted_key_placeholder_not_persisted(self):
        self.make([[(8,)]])
        result=self.call('save',{'name':'Fixture','env':{'[redacted]':'replacement'}})
        self.assertEqual(result.status,400,result.body)
        self.assertEqual(self.db.calls,[])

if __name__=='__main__':unittest.main()
