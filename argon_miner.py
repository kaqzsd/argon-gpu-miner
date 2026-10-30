"""ARG solo miner. One shared work fetcher, verified payout and headers."""
import argparse
import os
import logging
from logging.handlers import RotatingFileHandler
import concurrent.futures
import email.utils
import hashlib
import json
import secrets
import signal
import struct
import tempfile
import threading
import time
from pathlib import Path
import requests
from argon2.low_level import Type, hash_secret_raw
VERSION = '0.1.0'
WALLET = os.environ.get('ARG_ADDRESS', '')
SALT = b'Argonmine/pow/v1'


def require(ok, message='Invalid ARG data'):
    if not ok:
        raise ValueError(message)

def instance_lock(server, address):
    key = hashlib.sha256((server.rstrip('/') + '|' + address).encode()).hexdigest()[:24]
    stream = open(Path(tempfile.gettempdir()) / ('argon-solo-' + key + '.lock'), 'a+b')
    stream.write(b'0')
    stream.flush()
    stream.seek(0)
    try:
        if __import__('os').name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('Another miner for this address/server is already running on this host')
    return stream

def log(s):
    if logging.getLogger('argon').handlers:
        logging.getLogger('argon').info(s)
    else:
        print(time.strftime('%Y-%m-%d %H:%M:%S %z'), s, flush=True)

def pow_hash(header):
    return hash_secret_raw(header, SALT, 1, 8192, 1, 32, Type.ID, version=19)

def tagged(tag, data):
    h = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(h + h + data).digest()

def address_payload(address):
    require(address == address.lower() and address.startswith('arg1'), 'mainnet address required')
    (hrp, encoded) = address.rsplit('1', 1)
    vals = ['qpzry9x8gf2tvdw0s3jn54khce6mua7l'.index(c) for c in encoded]
    chk = 1
    for v in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + vals:
        top = chk >> 25
        chk = (chk & 33554431) << 5 ^ v
        for (i, g) in enumerate([996825010, 642813549, 513874426, 1027748829, 705979059]):
            if top >> i & 1:
                chk ^= g
    require(chk == 734539939 and vals[0] == 0, 'bad bech32m address')
    acc = bits = 0
    out = bytearray()
    for v in vals[1:-6]:
        acc = acc << 5 | v
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append(acc >> bits & 255)
    require(len(out) == 32 and bits < 5 and (not acc & (1 << bits) - 1), 'bad address payload')
    return bytes(out)

def verify_job(j, address):
    require(j['algorithm'] == 'argon2id' and j['network'] == 'mainnet', 'Invalid ARG job or mining parameters')
    prefix = bytes.fromhex(j['header_prefix'])
    require(len(prefix) == 112, 'Invalid ARG job or mining parameters')
    raw = bytes.fromhex(j['coinbase'])
    pos = 0

    def take(n):
        nonlocal pos
        r = raw[pos:pos + n]
        require(len(r) == n, 'truncated coinbase')
        pos += n
        return r

    def varint():
        value = 0
        for i in range(9):
            b = take(1)[0]
            value |= (b & 127) << 7 * i
            if b < 128:
                require(i == 0 or b != 0, 'Invalid ARG job or mining parameters')
                return value
        raise ValueError('bad varint')
    require(struct.unpack('<II', take(8)) == (1, j['height']), 'Invalid ARG job or mining parameters')
    require(varint() == 1 and take(32) == bytes(32) and (take(4) == b'\xff' * 4), 'Invalid ARG job or mining parameters')
    count = varint()
    require(count == (2 if j['height'] >= 300 else 1), 'unexpected/referral outputs')
    outputs = []
    for _ in range(count):
        amount = struct.unpack('<q', take(8))[0]
        kind = take(1)[0]
        payload = take(varint())
        require(amount >= 0 and kind == 0 and (len(payload) == 32), 'Invalid ARG job or mining parameters')
        outputs.append((amount, payload))
    no_witness = pos
    take(varint())
    require(pos == len(raw), 'Invalid ARG job or mining parameters')
    require(outputs[0][1] == address_payload(address), 'coinbase pays someone else')
    subsidy = 5000000000 >> j['height'] // 210000
    due = subsidy // 100 if j['height'] >= 300 else 0
    if due:
        require(outputs[-1] == (due, bytes.fromhex('7ad1a30d482a3fdb23efd2de4da69de2e9dc50d204d8cd82495183e6b3ffdf6c')), 'Invalid ARG job or mining parameters')
    require(outputs[0][0] >= subsidy - due - due, 'payout below observed server underclaim (1% subsidy)')
    for (tag, data, branch, offset) in [('txid', raw[:no_witness], j['merkle_branch'], 36), ('wtxid', raw, j['witness_branch'], 68)]:
        h = tagged('Argonmine/merkle/leaf', tagged('Argonmine/' + tag, data))
        require(len(branch) <= 32, 'Invalid ARG job or mining parameters')
        for s in branch:
            sibling = bytes.fromhex(s)
            require(len(sibling) == 32, 'Invalid ARG job or mining parameters')
            h = tagged('Argonmine/merkle/node', h + sibling)
        require(h == prefix[offset:offset + 32], 'merkle proof mismatch')
    bits = struct.unpack_from('<I', prefix, 108)[0]
    (exp, mantissa) = (bits >> 24, bits & 8388607)
    require(not bits & 8388608, 'Invalid ARG job or mining parameters')
    target = mantissa << 8 * (exp - 3) if exp >= 3 else mantissa >> 8 * (3 - exp)
    require(0 < target == int(j['target'], 16) < 2 ** 256, 'Invalid ARG job or mining parameters')
    j.update(prefix=prefix, target_int=target, payout=outputs[0][0], deficit=subsidy - due - outputs[0][0])
    return j

def retry_after(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError):
            return 0

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--address', default=WALLET, help='ARG payout address or ARG_ADDRESS environment variable')
    ap.add_argument('--version', action='version', version=VERSION)
    ap.add_argument('--device', type=int, default=0, help='OpenCL GPU index (see miner.sh devices)')
    ap.add_argument('--log-file', help='rotating log file, 10 MiB x 5 backups')
    ap.add_argument('--server', default='https://explorer.argonmine.com')
    ap.add_argument('--seconds', type=int, default=180, help='duration; 0 runs until stopped')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--interval', type=float, default=20)
    ap.add_argument('--backend', choices=['cpu', 'gpu'], default='gpu')
    ap.add_argument('--batch', type=int, default=1024)
    ap.add_argument('--output', default='result.json')
    args = ap.parse_args()
    if not args.address:
        ap.error('--address or ARG_ADDRESS is required; no built-in payout address')
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(args.log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger = logging.getLogger('argon')
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    require(args.seconds >= 0 and args.threads > 0 and (args.interval >= 20) and (args.device >= 0) and (args.batch >= 1), 'Invalid ARG job or mining parameters')
    address_payload(args.address)
    held_lock = instance_lock(args.server, args.address)
    stop = threading.Event()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: stop.set())
    lock = threading.Lock()
    current = [None]
    counts = dict(hashes=0, accepted=0, rejected=0, stale=0, requests=0, rate_limits=0, network_errors=0)
    (events, samples) = ([], [])
    started_at = time.time()
    start = time.monotonic()
    deadline = start + args.seconds if args.seconds else float('inf')
    log(f'{args.backend.upper()} SOLO payout={args.address}; duration={args.seconds}s')

    def fetcher():
        backoff = 30
        session = requests.Session()
        while not stop.is_set() and time.monotonic() < deadline:
            wait = args.interval
            try:
                counts['requests'] += 1
                t = time.monotonic()
                r = session.get(args.server + '/api/work', params={'address': args.address}, timeout=(10, 15))
                event = {'elapsed': round(time.monotonic() - start, 3), 'http': r.status_code, 'latency': round(time.monotonic() - t, 3)}
                if r.status_code == 429:
                    counts['rate_limits'] += 1
                    wait = max(retry_after(r.headers.get('Retry-After')), backoff) + secrets.randbelow(3000) / 1000
                    backoff = min(backoff * 2, 300)
                    with lock:
                        current[0] = None
                    event.update(body=r.text[:800], retry_after=r.headers.get('Retry-After'), wait=wait)
                    log(f'429: {r.text[:160]}; pause {wait:.1f}s')
                else:
                    r.raise_for_status()
                    j = verify_job(r.json(), args.address)
                    j['expires'] = time.monotonic() + args.interval
                    with lock:
                        current[0] = j
                    backoff = 30
                    event.update(height=j['height'], payout=j['payout'] / 100000000.0, expected_hashes=2 ** 256 / (j['target_int'] + 1))
                    log(f"Job height={j['height']} payout={j['payout'] / 100000000.0:.8f} expected_hashes={event['expected_hashes']:.0f}")
                    if j['deficit'] > 0:
                        log(f"NOTICE: coinbase underclaims {j['deficit'] / 100000000.0:.8f} ARG vs subsidy less treasury; no extra recipient")
                events.append(event)
            except Exception as exc:
                counts['network_errors'] += 1
                with lock:
                    current[0] = None
                log(f'work error: {type(exc).__name__}: {exc}')
                wait = backoff
                backoff = min(backoff * 2, 300)
                events.append({'elapsed': time.monotonic() - start, 'error': str(exc)})
            stop.wait(min(wait, max(0, deadline - time.monotonic())))
        session.close()

    def submit(j, n, digest):
        require(digest == pow_hash(j['prefix'] + struct.pack('<Q', n)), 'Invalid ARG job or mining parameters')
        require(int.from_bytes(digest, 'little') <= j['target_int'], 'Invalid ARG job or mining parameters')
        payload = {'job_id': j['job_id'], 'nonce': n, 'address': args.address}
        for attempt in range(3):
            if stop.is_set() or time.monotonic() >= j['expires']:
                return
            try:
                r = requests.post(args.server + '/api/work/submit', json=payload, timeout=(10, 15))
                if r.status_code in (429, 503):
                    stop.wait(max(2, retry_after(r.headers.get('Retry-After'))))
                    continue
                r.raise_for_status()
                result = r.json()
                events.append({'elapsed': time.monotonic() - start, 'submit': result, 'height': j['height'], 'nonce': n, 'digest': digest.hex()})
                if result.get('accepted'):
                    counts['accepted'] += 1
                elif result.get('stale'):
                    counts['stale'] += 1
                else:
                    counts['rejected'] += 1
                log(f'SUBMIT {result}')
                with lock:
                    if current[0] is j:
                        current[0] = None
                return
            except requests.RequestException as exc:
                log(f'submit transport error: {exc}')
                stop.wait(2)

    def cpu_worker():
        nonce = secrets.randbits(64)
        while not stop.is_set() and time.monotonic() < deadline:
            with lock:
                j = current[0]
            if j is None or time.monotonic() >= j['expires']:
                stop.wait(0.05)
                continue
            h = pow_hash(j['prefix'] + struct.pack('<Q', nonce))
            with lock:
                counts['hashes'] += 1
            if int.from_bytes(h, 'little') <= j['target_int']:
                submit(j, nonce, h)
            nonce = (nonce + 1) % 2 ** 64

    def save_snapshot():
        elapsed = time.monotonic() - start
        out = {'version': VERSION, 'updated_at': time.time(), 'started_at': started_at, 'running': not stop.is_set(), 'device': args.device, 'backend': args.backend, 'threads': args.threads, 'batch': args.batch, 'elapsed': elapsed, 'average_hs': counts['hashes'] / elapsed, 'address': args.address, **counts, 'events': events[-500:], 'samples': samples[-720:]}
        dest = Path(args.output)
        tmp = dest.with_suffix(dest.suffix + '.tmp')
        tmp.write_text(json.dumps(out, indent=2))
        tmp.replace(dest)
        return out
    gpu = None
    if args.backend == 'gpu':
        from gpu_backend import GPU
        gpu = GPU(args.batch, device_index=args.device)
        gpu.selftest()

    def gpu_worker():
        nonce = secrets.randbits(63)
        while not stop.is_set() and time.monotonic() < deadline:
            with lock:
                j = current[0]
            if j is None or time.monotonic() >= j['expires']:
                stop.wait(0.05)
                continue
            hashes = gpu.hashes(j['prefix'], nonce)
            with lock:
                counts['hashes'] += len(hashes)
            for (i, h) in enumerate(hashes):
                if int.from_bytes(h, 'little') <= j['target_int']:
                    submit(j, nonce + i, h)
            nonce = (nonce + len(hashes)) % (2 ** 64 - args.batch)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.threads + 2)
    save_snapshot()
    futures = [pool.submit(fetcher)]
    futures += [pool.submit(cpu_worker) for _ in range(args.threads)] if args.backend == 'cpu' else [pool.submit(gpu_worker)]
    (previous, last) = (0, start)
    next_snapshot = start + 10
    try:
        while not stop.wait(min(5, max(0, deadline - time.monotonic()))):
            now = time.monotonic()
            rate = (counts['hashes'] - previous) / (now - last)
            row = {'elapsed': round(now - start, 3), 'rate': round(rate, 2), **counts}
            samples.append(row)
            if len(samples) > 720:
                del samples[:-720]
            if len(events) > 500:
                del events[:-500]
            if now >= next_snapshot:
                save_snapshot()
                next_snapshot = now + 10
            log(f"{rate:.1f} H/s | hashes={counts['hashes']} accepted={counts['accepted']} rejected={counts['rejected']} stale={counts['stale']}")
            (previous, last) = (counts['hashes'], now)
            for future in futures:
                if future.done() and future.exception():
                    raise future.exception()
            if now >= deadline:
                break
    finally:
        stop.set()
        pool.shutdown(wait=True)
        out = save_snapshot()
        log(f"END average={out['average_hs']:.2f} H/s; {counts}")
        held_lock.close()
if __name__ == '__main__':
    main()
