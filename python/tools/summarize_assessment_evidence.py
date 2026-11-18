"""Summarize a local acceptance receipt without exposing its record contents."""
import argparse
import hashlib
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact')
    args=parser.parse_args()
    root=Path.cwd().resolve();path=(root/args.artifact).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>1048576:
        raise ValueError('Bounded artifact beneath working directory required')
    data=path.read_bytes();value=json.loads(data)
    operations=value['operations']
    if not isinstance(operations,dict):raise ValueError('Acceptance receipt required')
    print(json.dumps({'sha256':hashlib.sha256(data).hexdigest(),'task_status':value['task_status'],
        'operation_count':len(operations),'status_counts':{str(status):list(operations.values()).count(status) for status in set(operations.values())},
        'tool_calls':value['tool_calls']}))

if __name__=='__main__':main()
