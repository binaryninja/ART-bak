"""Inert bounded runtime tuning syntax check, NOT runtime launch or readiness.

Usage: PYTHONPATH=python python -m artex.runtime_config_check --check-config FILE
Only tuning values are accepted. No credentials, provider endpoints, host-policy
factories, transcript paths or execution flags. This never imports runtime,
provider or database modules, opens sockets, starts threads, or writes files.
Successful syntax validation does not authorize execution or validate host policy,
resources, database/schema, providers or exclusive ownership. Legacy CLI unchanged.
"""
import argparse
from dataclasses import dataclass
import json
import math
import os
import stat
import sys

MAX_CONFIG_BYTES=16384

class ConfigInvalid(ValueError):
    def __init__(self):super().__init__('Invalid runtime tuning configuration')

@dataclass(frozen=True)
class RuntimeTuning:
    version:int
    port:int
    max_connections:int
    socket_timeout:float
    pump_interval:float
    pump_max_per_tick:int
    read_max_rows:int
    read_max_bytes:int

def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ConfigInvalid()
        result[key]=value
    return result

def _reject_constant(value):raise ConfigInvalid()

def parse_runtime_tuning(data):
    """Pure syntax/bounds validation; no effectful resource or policy resolution."""
    try:
        if type(data) is not bytes or not 0<len(data)<=MAX_CONFIG_BYTES:raise ConfigInvalid()
        obj=json.loads(data.decode('utf-8'),object_pairs_hook=_object,parse_constant=_reject_constant)
        if type(obj) is not dict or set(obj)!={'version','listener','pump','reads'}:raise ConfigInvalid()
        if type(obj['version']) is not int or obj['version']!=1:raise ConfigInvalid()
        listener,pump,reads=obj['listener'],obj['pump'],obj['reads']
        for section,keys in ((listener,{'host','port','max_connections','socket_timeout'}),
                             (pump,{'interval','max_per_tick'}),(reads,{'max_rows','max_bytes'})):
            if type(section) is not dict or set(section)!=keys:raise ConfigInvalid()
        if listener['host']!='127.0.0.1':raise ConfigInvalid()
        def integer(value,low,high):
            if type(value) is not int or not low<=value<=high:raise ConfigInvalid()
            return value
        def number(value,low,high):
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ConfigInvalid()
            return float(value)
        return RuntimeTuning(1,integer(listener['port'],1,65535),integer(listener['max_connections'],1,32),
            number(listener['socket_timeout'],.1,60),number(pump['interval'],.01,60),
            integer(pump['max_per_tick'],1,1000),integer(reads['max_rows'],1,500),integer(reads['max_bytes'],1024,1048576))
    except Exception:
        raise ConfigInvalid() from None

class _Parser(argparse.ArgumentParser):
    def error(self,message):
        # argparse's normal error includes attacker-supplied argument values.
        self.exit(2,'Invalid arguments. Use --check-config FILE or --help.\n')

class _Once(argparse.Action):
    def __call__(self,parser,namespace,values,option_string=None):
        if getattr(namespace,self.dest,None) is not None:parser.error('Repeated option')
        setattr(namespace,self.dest,values)

def main(argv=None):
    parser=_Parser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--check-config',required=True,metavar='FILE',action=_Once,help='Read a bounded JSON tuning file; never launch')
    args=parser.parse_args(argv)
    try:
        descriptor=os.open(args.check_config,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        try:
            info=os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_CONFIG_BYTES:raise ConfigInvalid()
            with os.fdopen(descriptor,'rb',closefd=False) as source:data=source.read(MAX_CONFIG_BYTES+1)
        finally:os.close(descriptor)
        parse_runtime_tuning(data)
    except Exception:
        print('Runtime tuning check failed. File must be readable, bounded and valid; values are not logged.',file=sys.stderr)
        return 2
    print('Runtime tuning syntax valid. No runtime started; host authority, credentials, database/schema and provider readiness were NOT checked.')
    return 0

if __name__=='__main__':raise SystemExit(main())
