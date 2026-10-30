import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from argon_miner import pow_hash
from fixtures import ADDRESS,job

ROOT=Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.requests=0;self.submitted=[];self.limit=True
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,data):
                raw=json.dumps(data).encode();self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                if status==429:self.send_header('Retry-After','1')
                self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                outer.assertEqual(parse_qs(urlparse(self.path).query)['address'],[ADDRESS])
                outer.requests+=1
                self.reply(429,{'reason':'test'}) if outer.limit and outer.requests==1 else self.reply(200,job(easy=True))
            def do_POST(self):
                d=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.assertEqual(d['address'],ADDRESS)
                outer.assertIs(type(d['nonce']),int)
                j=job(easy=True);h=pow_hash(bytes.fromhex(j['header_prefix'])+struct.pack('<Q',d['nonce']))
                outer.assertLessEqual(int.from_bytes(h,'little'),int(j['target'],16))
                outer.submitted.append(d)
                self.reply(200,{'accepted':True,'stale':False,'hash':'synthetic-loopback-only'})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.env=dict(os.environ,NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.temp.cleanup()

    def test_rate_limit_then_valid_submission(self):
        output=self.root/'result.json'
        r=subprocess.run([sys.executable,str(ROOT/'argon_miner.py'),'--backend','cpu','--address',ADDRESS,'--server',self.url,'--seconds','40','--threads','1','--output',str(output)],capture_output=True,text=True,env=self.env,timeout=55)
        self.assertEqual(r.returncode,0,r.stderr)
        d=json.loads(output.read_text())
        self.assertEqual(d['rate_limits'],1)
        self.assertEqual(d['accepted'],1)
        self.assertEqual(d['network_errors'],0)
        self.assertFalse(d['running'])
        self.assertEqual(len(self.submitted),1)

    @unittest.skipUnless(sys.platform=='linux','Linux process identity management')
    def test_background_start_duplicate_status_stop(self):
        self.limit=False
        for f in ['manage.py','argon_miner.py']:shutil.copy(ROOT/f,self.root/f)
        def run(*args):return subprocess.run([sys.executable,str(self.root/'manage.py'),*args],capture_output=True,text=True,env=self.env,timeout=40)
        try:
            r=run('start','--address',ADDRESS,'--backend','cpu','--threads','1','--server',self.url)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue(json.loads(run('status','--json').stdout)['running'])
            self.assertNotEqual(run('start').returncode,0)
            r=run('stop');self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse(json.loads(run('status','--json').stdout)['running'])
        finally:run('stop')
