"""Synthetic protocol data. No user address, real work ID or private fixture."""
import struct
from argon_miner import tagged


def encode_address(payload):
    charset='qpzry9x8gf2tvdw0s3jn54khce6mua7l'
    acc=bits=0;data=[0]
    for b in payload:
        acc=(acc<<8)|b;bits+=8
        while bits>=5:
            bits-=5;data.append((acc>>bits)&31)
    if bits:data.append((acc<<(5-bits))&31)
    hrp='arg'
    chk=1
    for v in [ord(c)>>5 for c in hrp]+[0]+[ord(c)&31 for c in hrp]+data+[0]*6:
        top=chk>>25;chk=((chk&0x1ffffff)<<5)^v
        for i,g in enumerate([0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3]):
            if top>>i&1:chk^=g
    chk^=0x2bc830a3
    data += [(chk>>(5*(5-i)))&31 for i in range(6)]
    return hrp+'1'+''.join(charset[v] for v in data)


PAYLOAD=b'\x11'*32
ADDRESS=encode_address(PAYLOAD)


def job(easy=False):
    treasury=bytes.fromhex('7ad1a30d482a3fdb23efd2de4da69de2e9dc50d204d8cd82495183e6b3ffdf6c')
    cb=struct.pack('<II',1,1000)+b'\x01'+bytes(32)+b'\xff'*4+b'\x02'
    cb+=struct.pack('<qBB',4950000000,0,32)+PAYLOAD
    cb+=struct.pack('<qBB',50000000,0,32)+treasury
    txroot=tagged('Argonmine/merkle/leaf',tagged('Argonmine/txid',cb))
    cb+=b'\x04test'
    wroot=tagged('Argonmine/merkle/leaf',tagged('Argonmine/wtxid',cb))
    bits=0x207fffff if easy else 0x1e400000
    prefix=struct.pack('<I',1)+bytes(32)+txroot+wroot+struct.pack('<QI',1793397600,bits)
    target=(bits&0x7fffff) << (8*((bits>>24)-3))
    return {'job_id':'0'*32,'header_prefix':prefix.hex(),'target':f'{target:064x}',
            'height':1000,'algorithm':'argon2id','network':'mainnet','coinbase':cb.hex(),
            'merkle_branch':[],'witness_branch':[]}
