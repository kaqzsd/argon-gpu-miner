#!/usr/bin/env python3
"""Create allowlisted source/offline archives and SHA256SUMS for a release."""
import argparse
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
FILES=['README.md','LICENSE','ARGON2-GPU-LICENSE','requirements.txt','requirements-cpu.txt',
       'install.sh','miner.sh','manage.py','argon_miner.py','gpu_backend.py','gpu_io.cl',
       'argon2_kernel.cl','make_dist.py']


def archive(path,files,version):
    with path.open('wb') as output,gzip.GzipFile(filename='',mode='wb',fileobj=output,mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w') as tar:
        for source,name in sorted(files,key=lambda x:x[1]):
            raw=source.read_bytes()
            info=tarfile.TarInfo('argon-gpu-miner-'+version+'/'+name)
            info.size=len(raw);info.mode=0o755 if name in ('install.sh','miner.sh') else 0o644
            info.mtime=0
            tar.addfile(info,io.BytesIO(raw))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version',default='v0.1.0')
    ap.add_argument('--wheelhouse',type=Path)
    args=ap.parse_args()
    files=[(ROOT/name,name) for name in FILES]
    files += [(p,str(p.relative_to(ROOT))) for p in (ROOT/'tests').glob('*.py')]
    files += [(p,str(p.relative_to(ROOT))) for p in (ROOT/'.github/workflows').glob('*.yml')]
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    source=out/f'argon-gpu-miner-{args.version}-source.tar.gz'
    archive(source,files,args.version);assets=[source]
    if args.wheelhouse:
        wheels=sorted(args.wheelhouse.glob('*.whl'))
        if not wheels:raise ValueError('No wheels found')
        offline=out/f'argon-gpu-miner-{args.version}-linux-x86_64-py312-offline.tar.gz'
        archive(offline,files+[(p,'wheelhouse/'+p.name) for p in wheels],args.version)
        assets.append(offline)
    sums=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in assets)
    (out/'SHA256SUMS').write_text(sums)
    for p in assets:print(p.name,p.stat().st_size,'bytes')


if __name__=='__main__':main()
