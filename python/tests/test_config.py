import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from artex import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        env = patch.dict(os.environ, {'ARTEX_CONFIG': str(self.path), 'ARTEX_PG_DSN': '', 'ARTEX_SKILL_DIR': ''})
        env.start()
        self.addCleanup(env.stop)

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_precedence_and_missing(self):
        self.write({'database': {'host': '10.1.2.3', 'port': 6000, 'user': 'u', 'password': 'p', 'dbname': 'd', 'sslmode': 'require'}})
        self.assertEqual(config.postgres_dsn()[0], 'postgres://u:p@10.1.2.3:6000/d?sslmode=require')
        with patch.dict(os.environ, {'ARTEX_PG_DSN': ' postgres://envwins/x '}):
            self.assertEqual(config.postgres_dsn()[0], 'postgres://envwins/x')
        self.write({'database': {'dsn': 'postgres://full/dsn'}})
        self.assertEqual(config.postgres_dsn()[0], 'postgres://full/dsn')
        self.path.unlink()
        with self.assertRaises(ValueError):
            config.postgres_dsn()

    def test_defaults_and_escaping(self):
        db = config.Database(user='a@b', password='p:/#', dbname='d x')
        self.assertEqual(db.build_dsn(), 'postgres://a%40b:p%3A%2F%23@127.0.0.1:5432/d%20x?sslmode=disable')
        self.assertEqual(config.Database(host='::1').build_dsn(), 'postgres://[::1]:5432/?sslmode=disable')

    def test_invalid_config_and_types(self):
        self.path.write_text('{invalid')
        self.assertEqual(config.load(), config.Config())
        self.write({'database': {'port': True, 'host': 1}, 'skill_dir': []})
        self.assertEqual(config.load(), config.Config())

    def test_skill_directory_precedence(self):
        file_dir = Path(self.tmp.name) / 'file_skills'
        env_dir = Path(self.tmp.name) / 'env_skills'
        self.write({'skill_dir': str(file_dir)})
        self.assertEqual(config.skill_dir(), file_dir)
        self.assertTrue(file_dir.is_dir())
        with patch.dict(os.environ, {'ARTEX_SKILL_DIR': str(env_dir)}):
            self.assertEqual(config.skill_dir(), env_dir)
        self.assertTrue(env_dir.is_dir())

    def test_query_credentials_redacted(self):
        for query in ("user=alice&password=DEMO_SECRET",
                      "password=one&password=DEMO_SECRET",
                      "%70assword=DEMO_SECRET", "sslpassword=DEMO_SECRET",
                      "options=DEMO_SECRET"):
            for authority in ("localhost", "alice:OTHER_SECRET@localhost"):
                value = config.redact(f"postgres://{authority}/db?{query}#FRAGMENT_SECRET")
                self.assertNotIn("DEMO_SECRET", value)
                self.assertNotIn("OTHER_SECRET", value)
                self.assertNotIn("FRAGMENT_SECRET", value)
                self.assertIn("[redacted]", value)

    def test_redaction(self):
        self.assertEqual(config.redact('postgres://u:secret@host/d'), 'postgres://u:****@host/d')
        self.assertEqual(config.redact('postgres://u@host/d'), 'postgres://u@host/d')
        self.assertEqual(config.redact('host=localhost password=secret'), '[non-URI DSN redacted]')
        self.assertEqual(config.redact('password=secret'), '[non-URI DSN redacted]')
        self.assertNotIn('secret', config.redact('postgres://u:secret@[bad/d'))


if __name__ == '__main__':
    unittest.main()
