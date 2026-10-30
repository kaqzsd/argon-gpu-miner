import copy
import json
import os
import struct
import subprocess
import sys
import time
import unittest
from email.utils import formatdate
from pathlib import Path
from argon_miner import address_payload,instance_lock,retry_after,verify_job
from fixtures import ADDRESS,job

ROOT=Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.TestCase):
    def test_valid_job(self):
        self.assertEqual(verify_job(job(),ADDRESS)['payout'],4950000000)

    def test_tampering(self):
        for field,value in [('target','f'*64),('header_prefix','00'*112),('algorithm','argon2d'),('network','testnet')]:
            j=job();j[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):verify_job(j,ADDRESS)

    def test_payout_tampering(self):
        for offset in [46,55]:
            j=job();raw=bytearray.fromhex(j['coinbase']);raw[offset]^=1;j['coinbase']=raw.hex()
            with self.assertRaises(ValueError):verify_job(j,ADDRESS)

    def test_address(self):
        self.assertEqual(address_payload(ADDRESS),b'\x11'*32)
        with self.assertRaises(ValueError):address_payload(ADDRESS[:-1]+'q')

    def test_retry_after(self):
        self.assertEqual(retry_after('7200'),7200)
        self.assertEqual(retry_after('-1'),0)
        self.assertEqual(retry_after('bad'),0)
        self.assertGreater(retry_after(formatdate(time.time()+7200,usegmt=True)),7198)

    def test_nonce_uint64(self):
        n=2**64-1
        self.assertEqual(json.loads(json.dumps({'nonce':n}))['nonce'],n)
        self.assertEqual(struct.pack('<Q',n),b'\xff'*8)

    def test_optimized_python_keeps_checks(self):
        code="from fixtures import job,ADDRESS; from argon_miner import verify_job; j=job(); j['target']='f'*64; verify_job(j,ADDRESS)"
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'tests')]))
        r=subprocess.run([sys.executable,'-O','-c',code],env=env,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('ValueError',r.stderr)

    def test_single_instance(self):
        held=instance_lock('http://lock-test.local',ADDRESS)
        try:
            code=f"from argon_miner import instance_lock; instance_lock('http://lock-test.local',{ADDRESS!r})"
            r=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('already running',r.stderr)
        finally:held.close()
