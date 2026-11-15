"""Guard-only review; never connects to PostgreSQL or creates fixtures."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'integration'/'local_postgres_acceptance.py'
spec=importlib.util.spec_from_file_location('local_acceptance_review_target',PATH)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def arguments(**kw):
    values=dict(host='127.0.0.1',port=5432,database='artex_test_review',user='fixture',confirm_disposable='artex_test_review')
    values.update(kw)
    return SimpleNamespace(**values)


class Review(unittest.TestCase):
    def test_optimized_interpreter_refuses_before_any_connection(self):
        code='''import runpy,sys\nfrom types import SimpleNamespace\nm=runpy.run_path(sys.argv[1])\na=SimpleNamespace(host='127.0.0.1',port=5432,database='artex_test_review',user='fixture',confirm_disposable='artex_test_review')\ntry:m['validate_arguments'](a)\nexcept ValueError:sys.exit(0)\nsys.exit(5)'''
        env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
        result=subprocess.run([sys.executable,'-O','-c',code,str(PATH)],env=env,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_ambient_connection_and_remote_hosts_rejected(self):
        with patch.dict(os.environ,{'PGSERVICE':'unexpected'},clear=True):
            with self.assertRaises(ValueError):module.validate_arguments(arguments())
        with patch.dict(os.environ,{},clear=True):
            for host in ('localhost','192.0.2.1','127.0.0.1,remote','dbname=other'):
                with self.subTest(host=host),self.assertRaises(ValueError):module.validate_arguments(arguments(host=host))
            with self.assertRaises(ValueError):module.validate_arguments(arguments(confirm_disposable='other'))

    def test_actual_identity_mismatch_refuses(self):
        class Connection:
            def execute(self,sql):return self
            def fetchone(self):return ('another_db','fixture','127.0.0.1')
        with self.assertRaises(ValueError):module.verify_connection(Connection(),arguments())
