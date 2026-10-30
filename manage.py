#!/usr/bin/env python3
"""Linux rental-host controls. No host driver changes, wallets or autostart."""
import argparse
import ctypes
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/'runtime'
PIDFILE=RUNTIME/'process.json'
CONFIG=ROOT/'config.json'
SCRIPT=ROOT/'argon_miner.py'


def read_json(path,default=None):
    try:return json.loads(path.read_text())
    except (FileNotFoundError,ValueError):return default


def process_identity(pid):
    proc=Path('/proc')/str(pid)
    stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
    args=(proc/'cmdline').read_bytes().split(b'\0')
    return stat[19],stat[0],args


def owned_process():
    data=read_json(PIDFILE,{})
    if not data:return None
    try:
        ticks,state,args=process_identity(data['pid'])
        if ticks==data['starttime'] and state!='Z' and str(SCRIPT).encode() in args:
            return data['pid']
    except (FileNotFoundError,ProcessLookupError,KeyError,PermissionError):pass
    return None


def save_json(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,indent=2)+'\n')
    os.chmod(temp,0o600)
    temp.replace(path)


def doctor():
    print('Python:',sys.version.split()[0])
    if sys.platform!='linux':
        print('Background management supports Linux; run argon_miner.py directly on other systems.')
    try:
        r=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
        print(r.stdout.strip() or r.stderr.strip())
    except (FileNotFoundError,subprocess.TimeoutExpired):
        print('nvidia-smi unavailable. Select a rental image with a working NVIDIA driver.')
    try:
        ctypes.CDLL('libOpenCL.so.1')
        from gpu_backend import devices
        found=devices()
        if not found:raise RuntimeError('no OpenCL GPU devices')
        for i,d in enumerate(found):
            print(f'OpenCL GPU {i}: {d.name}, {d.global_mem_size/2**30:.1f} GiB, {d.version}')
        print('Environment OK. Run ./miner.sh self-test --full before mining on a new GPU.')
        return 0
    except Exception as exc:
        print('OpenCL unavailable:',str(exc))
        print('Missing libOpenCL.so.1: sudo bash install.sh --system-deps')
        print('If NVIDIA OpenCL is absent: ask the rental host/image to expose libnvidia-opencl.so.1 and its NVIDIA ICD.')
        print('The installer does not replace the host driver or fall back to CPU mining.')
        return 1


def start(args):
    if sys.platform!='linux':raise RuntimeError('Background start requires Linux')
    pid=owned_process()
    if pid:raise RuntimeError(f'Already running (PID {pid}); stop it before changing configuration')
    config=read_json(CONFIG,{})
    for key in ['address','batch','device','threads','backend','server','interval']:
        value=getattr(args,key)
        if value is not None:config[key]=value
    defaults={'batch':1024,'device':0,'threads':8,'backend':'gpu','server':'https://explorer.argonmine.com','interval':20}
    config={**defaults,**config}
    config['address']=config.get('address') or os.environ.get('ARG_ADDRESS','')
    from argon_miner import address_payload,require
    require(bool(config['address']),'Set --address YOUR_ARG_ADDRESS; no default payout address')
    address_payload(config['address'])
    require(config['batch']>0 and config['device']>=0 and config['threads']>0 and config['interval']>=20,'invalid configuration')
    save_json(CONFIG,config)
    command=[sys.executable,'-u',str(SCRIPT),'--seconds','0','--output',str(RUNTIME/'status.json'),'--log-file',str(RUNTIME/'miner.log')]
    for k,v in config.items():
        if k in defaults or k=='address':command.extend(['--'+k,str(v)])
    with (RUNTIME/'launcher.log').open('ab') as out:
        p=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    time.sleep(.2)
    if p.poll() is not None:
        raise RuntimeError('Startup failed; read runtime/launcher.log')
    ticks,_,_=process_identity(p.pid)
    save_json(PIDFILE,{'pid':p.pid,'starttime':ticks,'script':str(SCRIPT)})
    time.sleep(2)
    if p.poll() is not None:
        raise RuntimeError('Startup/self-test failed; read runtime/launcher.log and runtime/miner.log')
    print(f'Started PID {p.pid}. Payout: {config["address"]}')
    print('GPU initialization/self-test may still be running. Verify: ./miner.sh status; ./miner.sh logs')
    return 0


def stop():
    pid=owned_process()
    if not pid:
        print('No managed miner is running.');return 0
    os.kill(pid,signal.SIGTERM)
    for _ in range(350):
        if not owned_process():
            print(f'Stopped PID {pid}.');return 0
        time.sleep(.1)
    raise RuntimeError('Miner is still shutting down; check status/logs. No unrelated process was signalled.')


def status(as_json=False):
    pid=owned_process()
    snapshot=read_json(RUNTIME/'status.json',{})
    age=time.time()-snapshot.get('updated_at',0) if snapshot else None
    result={'running':pid is not None,'pid':pid,'snapshot_age_seconds':age,'snapshot':snapshot}
    if as_json:print(json.dumps(result,indent=2));return 0
    print('RUNNING' if pid else 'STOPPED','PID',pid or '-')
    if snapshot:
        print('Payout:',snapshot.get('address'))
        if pid and age<30 and snapshot.get('samples'):
            print(f"Latest: {snapshot['samples'][-1]['rate']:,.1f} H/s; average: {snapshot['average_hs']:,.1f} H/s")
        else:print('Snapshot is historical or initialization is in progress; inspect logs.')
        print('Hashes:',snapshot.get('hashes'),'accepted:',snapshot.get('accepted'),'rejected:',snapshot.get('rejected'),'stale:',snapshot.get('stale'),'429:',snapshot.get('rate_limits'))
    return 0


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='action',required=True)
    st=sub.add_parser('start')
    st.add_argument('--address');st.add_argument('--batch',type=int);st.add_argument('--device',type=int)
    st.add_argument('--threads',type=int);st.add_argument('--backend',choices=['gpu','cpu'])
    st.add_argument('--server');st.add_argument('--interval',type=float)
    sub.add_parser('stop')
    sub.add_parser('status').add_argument('--json',action='store_true')
    logs=sub.add_parser('logs');logs.add_argument('--lines',type=int,default=30);logs.add_argument('--follow',action='store_true')
    sub.add_parser('doctor');sub.add_parser('devices')
    for action in ['self-test','benchmark']:
        parser=sub.add_parser(action)
        parser.add_argument('--device',type=int,default=0)
        if action=='self-test':
            parser.add_argument('--batch',type=int,default=256);parser.add_argument('--full',action='store_true')
        else:
            parser.add_argument('--batches',default='256,512,1024,2048');parser.add_argument('--seconds',type=float,default=5)
    args=ap.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.action=='doctor':return doctor()
    if args.action in ['devices','self-test','benchmark']:
        command=[sys.executable,str(ROOT/'gpu_backend.py'),args.action]
        for k,v in vars(args).items():
            if k=='action' or v is False:continue
            command.append('--'+k)
            if v is not True:command.append(str(v))
        return subprocess.call(command,cwd=ROOT)
    if args.action=='status':return status(args.json)
    if args.action=='logs':
        path=RUNTIME/'miner.log'
        if not path.exists():print('No mining log yet; inspect runtime/launcher.log if startup failed.');return 0
        return subprocess.call(['tail','-n',str(args.lines)]+(['-F'] if args.follow else [])+[str(path)])
    with (RUNTIME/'control.lock').open('a') as held:
        fcntl.flock(held,fcntl.LOCK_EX)
        return start(args) if args.action=='start' else stop()


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,RuntimeError) as exc:
        print('ERROR:',exc,file=sys.stderr);sys.exit(1)
