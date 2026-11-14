"""Per-call Claude PreToolUse boundary; never changes a native permission profile."""
from pathlib import Path
import argparse,json,os,shlex,sys

def output(**fields):
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse',**fields}}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);a=parser.parse_args()
    try:
        cfg=json.loads(a.config.read_text());event=json.load(sys.stdin);tool=event.get('tool_name');data=event.get('tool_input',{})
        roots=[Path(p).resolve(strict=True)for p in cfg['write_dirs']]
        if tool in ('Read','Glob','Grep'):
            print('{}');return 0
        if tool in ('Write','Edit'):
            name=data.get('file_path')
            if not isinstance(name,str):raise ValueError('missing file_path')
            path=Path(name)
            if not path.is_absolute():path=Path(event.get('cwd',cfg['workspace']))/path
            path=path.resolve()
            if not any(path.is_relative_to(root)for root in roots):
                output(permissionDecision='deny',permissionDecisionReason='code-builder writes are limited to the declared source/check directories');return 0
            print('{}');return 0
        if tool!='Bash':raise ValueError('unsupported guarded tool')
        command=data.get('command')
        if not isinstance(command,str):raise ValueError('missing command')
        ledger=Path(cfg['ledger'])
        previous=[json.loads(line)for line in ledger.read_text().splitlines()]if ledger.exists()else[]
        if any(command==row['wrapped']for row in previous):
            print('{}');return 0
        argv=[sys.executable,cfg['guard'],'--tmpdir',cfg['private_tmp']]
        for root in [*roots,*[Path(p).resolve(strict=True)for p in cfg.get('runtime_write_dirs',[])]]:argv+=['--write-dir',str(root)]
        argv+=['--','/bin/bash','-c',command]
        wrapped=shlex.join(argv)
        # O_APPEND makes one short per-call record; the ledger/config/helper live
        # outside every granted model write subtree.
        with ledger.open('a')as handle:handle.write(json.dumps({'original':command,'wrapped':wrapped})+'\n')
        output(updatedInput={**data,'command':wrapped});return 0
    except Exception as error:
        output(permissionDecision='deny',permissionDecisionReason='code-builder guard could not validate this call: '+type(error).__name__);return 0

if __name__=='__main__':raise SystemExit(main())
