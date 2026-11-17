"""Standalone config contracts derived from config/config.go and encoding/json.

All file and environment inputs are synthetic; no database or startup is used.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from artex import config


class ConfigParsingParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "synthetic.json"
        environment = patch.dict(os.environ, {
            "ARTEX_CONFIG": str(self.path),
            "ARTEX_PG_DSN": "",
            "ARTEX_SKILL_DIR": "",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def load_text(self, text):
        self.path.write_text(text, encoding="utf-8")
        return config.load()

    def test_case_variants_match_the_go_json_tags(self):
        result = self.load_text('''{
            "Database": {"DSN": "synthetic", "Host": "db.test", "PORT": 6001,
                "USER": "alice", "Password": "example", "DBName": "demo",
                "SSLMode": "require"}, "SKILL_DIR": "synthetic-skills"}''')
        self.assertEqual(result, config.Config(config.Database(
            dsn="synthetic", host="db.test", port=6001, user="alice",
            password="example", dbname="demo", sslmode="require"), "synthetic-skills"))

    def test_duplicate_database_objects_merge_in_document_order(self):
        result = self.load_text('''{"database":{"host":"db.test","user":"first"},
            "database":{"port":6002}, "DATABASE":{"user":"last","dbname":"demo"}}''')
        self.assertEqual(result.database, config.Database(
            host="db.test", user="last", port=6002, dbname="demo"))

    def test_duplicate_field_case_and_exact_spellings_keep_last_valid_value(self):
        for members in ('"host":"first","HOST":"second","host":"last"',
                        '"HOST":"first","host":"second","HOST":"last"'):
            with self.subTest(members=members):
                self.assertEqual(self.load_text('{"database":{' + members + '}}').database.host, "last")

    def test_later_null_or_wrong_type_does_not_erase_accepted_scalars(self):
        for invalid in ("null", "false", "[]", "{}", "42", "1.5"):
            with self.subTest(invalid=invalid):
                result = self.load_text('{"skill_dir":"kept","skill_dir":' + invalid
                    + ',"database":{"host":"kept","host":' + invalid + '}}')
                self.assertEqual(result.skill_dir, "kept")
                self.assertEqual(result.database.host, "kept")

    def test_later_null_or_wrong_type_does_not_erase_database_struct(self):
        for invalid in ("null", "false", "[]", '"wrong"', "42"):
            with self.subTest(invalid=invalid):
                result = self.load_text('{"database":{"host":"kept"},"database":' + invalid + '}')
                self.assertEqual(result.database.host, "kept")

    def test_port_skips_incompatible_and_native_integer_overflow_values(self):
        invalid_values = ("null", "true", '"6000"', "6000.0", "6e3", "[]", "{}",
                          str(sys.maxsize + 1), str(-sys.maxsize - 2), "9" * 5000)
        for invalid in invalid_values:
            with self.subTest(invalid=invalid[:30]):
                result = self.load_text('{"database":{"host":"kept","port":6000,"port":' + invalid + '}}')
                self.assertEqual(result.database, config.Database(host="kept", port=6000))

    def test_native_integer_boundaries_zero_and_negative_port_are_not_policy_validated(self):
        for port in (-sys.maxsize - 1, sys.maxsize, -1, 0, 65536):
            with self.subTest(port=port):
                self.assertEqual(self.load_text(json.dumps({"database": {"port": port}})).database.port, port)

    def test_go_simple_fold_aliases_do_not_expand_multicharacter_unicode(self):
        result = self.load_text('''{"ſKILL_DIR":"kept","database":{
            "hoſt":"db.test","paſſword":"kept","paßword":"wrong","ßlmode":"wrong"}}''')
        self.assertEqual(result.skill_dir, "kept")
        self.assertEqual(result.database.host, "db.test")
        self.assertEqual(result.database.password, "kept")
        self.assertEqual(result.database.sslmode, "")

    def test_unknown_fields_and_arrays_of_pairs_are_not_struct_members(self):
        result = self.load_text('''{"SkillDir":"wrong","skill-dir":"wrong",
            "unknown":{"host":"wrong"},"database":[["host","wrong"]]}''')
        self.assertEqual(result, config.Config())
        self.assertEqual(self.load_text('[["database",{"host":"wrong"}]]'), config.Config())

    def test_invalid_document_does_not_apply_partial_values(self):
        for text in ('{"database":{"host":"wrong"},}',
                     '{"database":{"host":"wrong"}} trailing',
                     '{"database":{"host":"wrong"},"extra":NaN}',
                     '{"extra":Infinity,"database":{"host":"wrong"}}',
                     '{"database":{"host":"wrong","port":-Infinity}}'):
            with self.subTest(text=text):
                self.assertEqual(self.load_text(text), config.Config())

    def test_missing_unreadable_and_nonobject_documents_use_zero_config(self):
        self.assertEqual(config.load(), config.Config())
        with patch.object(Path, "read_text", side_effect=OSError("synthetic unreadable")):
            self.assertEqual(config.load(), config.Config())
        for text in ("null", "false", "1", '"text"', "[]"):
            with self.subTest(text=text):
                self.assertEqual(self.load_text(text), config.Config())

    def test_case_and_merge_flow_through_existing_dsn_resolution(self):
        self.load_text('''{"DATABASE":{"HOST":"db.test","USER":"alice"},
            "database":{"port":6000,"dbname":"demo"}}''')
        self.assertEqual(config.postgres_dsn(), (
            "postgres://alice@db.test:6000/demo?sslmode=disable",
            f"configuration file {self.path} (database fields)"))

    def test_explicit_dsn_and_environment_precedence_remain_unchanged(self):
        self.load_text('{"DATABASE":{"DSN":"  postgres://file.test/demo  ","HOST":"ignored"}}')
        self.assertEqual(config.postgres_dsn(), ("postgres://file.test/demo",
            f"configuration file {self.path} (database.dsn)"))
        with patch.dict(os.environ, {"ARTEX_PG_DSN": " postgres://env.test/demo "}):
            with patch.object(Path, "read_text", side_effect=AssertionError("must not read file")):
                self.assertEqual(config.postgres_dsn(), ("postgres://env.test/demo",
                    "environment variable ARTEX_PG_DSN"))

    def test_nonidentifying_fields_do_not_enable_an_implicit_database(self):
        self.load_text('{"database":{"port":6000,"password":"synthetic","sslmode":"require"}}')
        with self.assertRaisesRegex(ValueError, "No database configuration"):
            config.postgres_dsn()

    def test_standalone_dataclass_serialization_remains_canonical_and_independent(self):
        result = self.load_text('{"Database":{"HOST":"db.test"},"SKILL_DIR":"synthetic"}')
        encoded = asdict(result)
        self.assertEqual(set(encoded), {"database", "skill_dir"})
        self.assertEqual(encoded["database"]["host"], "db.test")
        self.assertEqual(self.load_text(json.dumps(encoded)), result)
        encoded["database"]["host"] = "changed"
        self.assertEqual(result.database.host, "db.test")
        self.assertEqual(config.Config().database.host, "")


class ConfigDsnConformanceTests(unittest.TestCase):
    def test_database_zero_defaults_and_password_without_user(self):
        self.assertEqual(config.Database().build_dsn(), "postgres://127.0.0.1:5432/?sslmode=disable")
        self.assertEqual(config.Database(password="synthetic").build_dsn(), config.Database().build_dsn())

    def test_dsn_components_roundtrip_without_network(self):
        for user, password, name, sslmode in (
            ("alice", "example", "demo", "require"),
            ("a@b:/?#%", "p@:/?#%", "d /?#+%", "verify full&option"),
            ("用户", "示例", "数据库", "模式"),
        ):
            with self.subTest(user=user):
                result = urlsplit(config.Database(host="db.test", port=6000,
                    user=user, password=password, dbname=name, sslmode=sslmode).build_dsn())
                self.assertEqual((result.scheme, result.hostname, result.port), ("postgres", "db.test", 6000))
                self.assertEqual((unquote(result.username), unquote(result.password)), (user, password))
                self.assertEqual(unquote(result.path), "/" + name)
                self.assertEqual(parse_qs(result.query), {"sslmode": [sslmode]})

    def test_existing_ipv6_normalization_is_preserved(self):
        for host in ("::1", "[::1]"):
            self.assertEqual(config.Database(host=host).build_dsn(), "postgres://[::1]:5432/?sslmode=disable")

    def test_existing_masking_and_fail_closed_differences_are_preserved(self):
        self.assertEqual(config.redact("postgres://alice:example@db.test/demo"),
                         "postgres://alice:****@db.test/demo")
        self.assertEqual(config.redact("postgres://alice@db.test/demo?sslmode=require#example"),
                         "postgres://alice@db.test/demo?[redacted]#[redacted]")
        self.assertEqual(config.redact("host=db.test password=example"), "[non-URI DSN redacted]")
        self.assertEqual(config.redact("postgres://alice:example@[broken/demo"), "[invalid DSN redacted]")
