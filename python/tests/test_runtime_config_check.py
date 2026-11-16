import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from artex.runtime_config_check import parse_runtime_tuning,ConfigInvalid,main,MAX_CONFIG_BYTES

CONFIG={'version':1,'listener':{'host':'127.0.0.1','port':8787,'max_connections':8,'socket_timeout':10},'pump':{'interval':1,'max_per_tick':8},'reads':{'max_rows':500,'max_bytes':1048576}}
class Tests(unittest.TestCase):
    def test_valid_pure_no_runtime_imports(self):
        value=parse_runtime_tuning(json.dumps(CONFIG).encode());self.assertEqual(value.port,8787)
        with patch('builtins.open',side_effect=AssertionError('No I/O')):
            self.assertEqual(parse_runtime_tuning(json.dumps(CONFIG).encode()),value)
        code="import sys;from artex.runtime_config_check import parse_runtime_tuning; assert not any(n.startswith(('artex.server','artex.db','artex.providers','artex.agent')) for n in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable,'-c',code],capture_output=True).returncode,0)
    def test_exact_keys_and_no_credential_or_factory_options(self):
        for key in ('dsn','signing_key','provider','factory','execute','transcript_root'):
            value=copy.deepcopy(CONFIG);value[key]='PRIVATE_SECRET'
            with self.assertRaises(ConfigInvalid):parse_runtime_tuning(json.dumps(value).encode())
        for section in ('listener','pump','reads'):
            value=copy.deepcopy(CONFIG);value[section]['unknown']='PRIVATE'
            with self.assertRaises(ConfigInvalid):parse_runtime_tuning(json.dumps(value).encode())
            value=copy.deepcopy(CONFIG);value.pop(section)
            with self.assertRaises(ConfigInvalid):parse_runtime_tuning(json.dumps(value).encode())
    def test_duplicate_unicode_nan_depth_and_size(self):
        samples=[b'{"version":1,"version":1}',b'{"version":1,"\\u0076ersion":1}',b'{"private":NaN}',b'\xff',b'[]',b'null',b'['*2000+b']'*2000,b' '*MAX_CONFIG_BYTES+b'{}']
        for sample in samples:
            with self.assertRaisesRegex(ConfigInvalid,'^Invalid runtime tuning configuration$'):parse_runtime_tuning(sample)
    def test_types_bounds_and_nonloopback(self):
        for section,key,values in [('listener','port',[True,0,65536,'8787',1.5]),('listener','host',['0.0.0.0','localhost','::1']),('listener','max_connections',[0,33,True]),('listener','socket_timeout',[0,61,True,float('inf')]),('pump','interval',[0,61,False]),('pump','max_per_tick',[0,1001]),('reads','max_rows',[0,501]),('reads','max_bytes',[1023,1048577])]:
            for bad in values:
                value=copy.deepcopy(CONFIG);value[section][key]=bad
                with self.assertRaises(ConfigInvalid):parse_runtime_tuning(json.dumps(value).encode())
    def test_cli_no_side_effects_and_redacted_failures(self):
        import contextlib,io
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'PRIVATE_SECRET';path.write_text(json.dumps(CONFIG))
            before=path.read_bytes();output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(['--check-config',str(path)]),0)
            self.assertIn('No runtime started',output.getvalue());self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(list(Path(folder).iterdir())),1)
            path.write_text('{"PRIVATE_SECRET":1}');error=io.StringIO()
            with contextlib.redirect_stderr(error):self.assertEqual(main(['--check-config',str(path)]),2)
            self.assertNotIn('PRIVATE_SECRET',error.getvalue())
            error=io.StringIO()
            with contextlib.redirect_stderr(error),self.assertRaises(SystemExit):main(['--serve','PRIVATE_SECRET'])
            self.assertNotIn('PRIVATE_SECRET',error.getvalue())

    def test_cli_refuses_special_files_and_symlink(self):
        import os,contextlib,io
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fifo';os.mkfifo(path)
            actual=Path(folder)/'actual';actual.write_text(json.dumps(CONFIG))
            link=Path(folder)/'link';link.symlink_to(actual)
            for target in (path,link,Path(folder)):
                with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(['--check-config',str(target)]),2)

    def test_cli_no_abbreviations_or_duplicate_check_target(self):
        import contextlib,io
        with tempfile.TemporaryDirectory() as folder:
            valid=Path(folder)/'valid';valid.write_text(json.dumps(CONFIG))
            for args in (['--check',str(valid)],['--check-config','PRIVATE_INVALID','--check-config',str(valid)],['--check-config='+str(valid),'--check-config='+str(valid)]):
                error=io.StringIO()
                with patch('os.open',side_effect=AssertionError('Must reject before file access')),contextlib.redirect_stderr(error),self.assertRaises(SystemExit) as caught:main(args)
                self.assertEqual(caught.exception.code,2);self.assertNotIn('PRIVATE_INVALID',error.getvalue())
    def test_ancestor_symlink_is_not_a_confinement_boundary(self):
        import contextlib,io
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)/'real';directory.mkdir();(directory/'config').write_text(json.dumps(CONFIG))
            link=Path(folder)/'alias';link.symlink_to(directory,target_is_directory=True)
            with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['--check-config',str(link/'config')]),0)
