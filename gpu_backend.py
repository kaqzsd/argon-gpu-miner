"""OpenCL Argon2id implementation; every returned byte tested against libargon2."""
import argparse
import json
import os
import secrets
import struct
import time
from pathlib import Path

import numpy as np
import pyopencl as cl

from argon_miner import pow_hash, log, require


def devices():
    found=[]
    for platform in cl.get_platforms():
        try:
            found.extend(platform.get_devices(device_type=cl.device_type.GPU))
        except cl.LogicError:
            continue
    return found


class GPU:
    def __init__(self, batch=1024, jobs_per_group=1, device_index=0):
        self.batch = batch
        self.jobs_per_group = jobs_per_group
        require(batch>0 and jobs_per_group>0 and batch % jobs_per_group == 0,'invalid batch/workgroup size')
        available=devices()
        require(0<=device_index<len(available),'OpenCL GPU not found; run ./miner.sh doctor')
        self.device = available[device_index]
        require(batch*8388608<=min(self.device.max_mem_alloc_size,int(self.device.global_mem_size*.85)),
                'Batch exceeds GPU allocation limit; reduce --batch (512=4 GiB, 1024=8 GiB)')
        self.ctx = cl.Context([self.device])
        self.q = cl.CommandQueue(self.ctx)
        mf = cl.mem_flags
        self.memory = cl.Buffer(self.ctx, mf.READ_WRITE, batch*8388608)
        self.refs = cl.Buffer(self.ctx, mf.READ_WRITE, 4096*8)
        self.prefix = cl.Buffer(self.ctx, mf.READ_ONLY, 112)
        self.output = cl.Buffer(self.ctx, mf.WRITE_ONLY, batch*32)
        self.result = np.empty((batch,32),dtype=np.uint8)
        root = Path(__file__).parent
        src = (root/'argon2_kernel.cl').read_text()
        self.program = cl.Program(self.ctx, src).build(options=['-DARGON2_TYPE=2','-DARGON2_VERSION=19'])
        self.io = cl.Program(self.ctx,(root/'gpu_io.cl').read_text()).build()
        self.precompute = cl.Kernel(self.program,'argon2_precompute_kernel')
        self.kernel = cl.Kernel(self.program,'argon2_kernel_oneshot_precompute')
        self.init = cl.Kernel(self.io,'init_arg')
        self.finish = cl.Kernel(self.io,'finish_arg')
        self.precompute(self.q,(32*2*16,), (32,), cl.LocalMemory(256), self.refs,np.uint32(1),np.uint32(1),np.uint32(2048)).wait()
        log(f'GPU {self.device.name}; batch={batch}; workspace={batch*8} MiB; group={jobs_per_group}')

    def hashes(self,prefix,nonce):
        cl.enqueue_copy(self.q,self.prefix,prefix)
        self.init(self.q,(self.batch,),None,self.memory,self.prefix,np.uint64(nonce))
        self.kernel(self.q,(32,self.batch),(32,self.jobs_per_group),cl.LocalMemory(256*self.jobs_per_group),self.memory,self.refs,np.uint32(1),np.uint32(1),np.uint32(2048))
        self.finish(self.q,(self.batch,),None,self.memory,self.output)
        cl.enqueue_copy(self.q,self.result,self.output).wait()
        return [r.tobytes() for r in self.result]

    def selftest(self,full=False):
        tested=0
        for base in [0, 2**32-5, 2**64-self.batch]:
            prefix=secrets.token_bytes(112)
            got=self.hashes(prefix,base)
            indices=sorted({0,1,self.batch//2,self.batch-1,*[secrets.randbelow(self.batch) for _ in range(12)]})
            if full:
                indices=range(self.batch)
            for i in indices:
                expected=pow_hash(prefix+struct.pack('<Q',base+i))
                require(got[i]==expected, f'GPU mismatch index={i} nonce={base+i}: {got[i].hex()} != {expected.hex()}')
                tested+=1
        log(f'GPU/CPU self-test passed: {tested} complete 32-byte hashes across 3 headers and nonce boundaries')
        return tested


def main():
    ap=argparse.ArgumentParser(description='List GPUs, verify hashes, or benchmark without network access')
    ap.add_argument('action',choices=['devices','self-test','benchmark'])
    ap.add_argument('--device',type=int,default=0)
    ap.add_argument('--batch',type=int,default=256)
    ap.add_argument('--full',action='store_true')
    ap.add_argument('--batches',default='256,512,1024,2048')
    ap.add_argument('--seconds',type=float,default=5)
    ap.add_argument('--output',default='runtime/benchmark.json')
    args=ap.parse_args()
    if args.action=='devices':
        for i,d in enumerate(devices()):
            print(f'{i}: {d.name}; {d.global_mem_size/2**30:.1f} GiB; driver {d.driver_version}; {d.version}')
        return
    if args.action=='self-test':
        GPU(args.batch,device_index=args.device).selftest(full=args.full)
        return
    require(args.seconds>0,'benchmark seconds must be positive')
    results=[]
    for batch in [int(v) for v in args.batches.split(',')]:
        gpu=GPU(batch,device_index=args.device)
        tested=gpu.selftest()
        prefix=secrets.token_bytes(112)
        t=time.monotonic()
        n=0
        while time.monotonic()-t < args.seconds:
            gpu.hashes(prefix,n)
            n+=batch
        row={'batch':batch,'hashes':n,'seconds':time.monotonic()-t,'tested':tested,'device':gpu.device.name}
        row['hs']=n/row['seconds']
        log(json.dumps(row))
        results.append(row)
        del gpu
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(results,indent=2))
    print('Fastest tested batch:',max(results,key=lambda row:row['hs'])['batch'])


if __name__=='__main__':
    main()
