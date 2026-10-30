// ARG fixed parameters: Argon2id v19, t=1, m=8192 KiB, p=1, 32 bytes.
__constant ulong IV[8] = {
  0x6a09e667f3bcc908UL,0xbb67ae8584caa73bUL,0x3c6ef372fe94f82bUL,0xa54ff53a5f1d36f1UL,
  0x510e527fade682d1UL,0x9b05688c2b3e6c1fUL,0x1f83d9abfb41bd6bUL,0x5be0cd19137e2179UL};
__constant uchar SIGMA[192] = {
 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
 14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3,
 11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4,
 7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8,
 9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13,
 2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9,
 12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11,
 13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10,
 6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5,
 10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0,
 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
 14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3};
ulong rr(ulong x,uint n){return (x>>n)|(x<<(64-n));}
#define BG(a,b,c,d,x,y) a=a+b+x;d=rr(d^a,32);c=c+d;b=rr(b^c,24);a=a+b+y;d=rr(d^a,16);c=c+d;b=rr(b^c,63);
void b2(const uchar *in,uint n,uchar *out,uint outlen){
 ulong h[8],v[16],m[16];
 for(uint i=0;i<8;i++)h[i]=IV[i];
 h[0]^=0x01010000|outlen;
 for(uint off=0;off<n;off+=128){
   uint take=min((uint)128,n-off);
   for(uint i=0;i<16;i++){
     m[i]=0;
     for(uint k=0;k<8;k++)if(i*8+k<take)m[i]|=((ulong)in[off+i*8+k])<<(k*8);
   }
   for(uint i=0;i<8;i++){v[i]=h[i];v[i+8]=IV[i];}
   v[12]^=(ulong)(off+take);
   if(off+take==n)v[14]=~v[14];
   for(uint r=0;r<12;r++){
     __constant uchar *s=SIGMA+r*16;
     BG(v[0],v[4],v[8],v[12],m[s[0]],m[s[1]]);
     BG(v[1],v[5],v[9],v[13],m[s[2]],m[s[3]]);
     BG(v[2],v[6],v[10],v[14],m[s[4]],m[s[5]]);
     BG(v[3],v[7],v[11],v[15],m[s[6]],m[s[7]]);
     BG(v[0],v[5],v[10],v[15],m[s[8]],m[s[9]]);
     BG(v[1],v[6],v[11],v[12],m[s[10]],m[s[11]]);
     BG(v[2],v[7],v[8],v[13],m[s[12]],m[s[13]]);
     BG(v[3],v[4],v[9],v[14],m[s[14]],m[s[15]]);
   }
   for(uint i=0;i<8;i++)h[i]^=v[i]^v[i+8];
 }
 for(uint i=0;i<outlen;i++)out[i]=(uchar)(h[i/8]>>(8*(i%8)));
}
void w32(uchar *p,uint n){for(uint i=0;i<4;i++)p[i]=(uchar)(n>>(8*i));}
__kernel void init_arg(__global uchar *memory,__global const uchar *prefix,ulong nonce){
 uint job=get_global_id(0);
 uchar input[176],h0[64],seed[76],v[64];
 w32(input,1);w32(input+4,32);w32(input+8,8192);w32(input+12,1);
 w32(input+16,19);w32(input+20,2);w32(input+24,120);
 for(uint i=0;i<112;i++)input[28+i]=prefix[i];
 ulong nn=nonce+job;
 for(uint i=0;i<8;i++)input[140+i]=(uchar)(nn>>(8*i));
 w32(input+148,16);
 const uchar salt[16]={'A','r','g','o','n','m','i','n','e','/','p','o','w','/','v','1'};
 for(uint i=0;i<16;i++)input[152+i]=salt[i];
 w32(input+168,0);w32(input+172,0);
 b2(input,176,h0,64);
 w32(seed,1024);
 for(uint i=0;i<64;i++)seed[4+i]=h0[i];
 w32(seed+72,0);
 for(uint block=0;block<2;block++){
   w32(seed+68,block);b2(seed,76,v,64);
   __global uchar *dst=memory+(size_t)job*8388608+block*1024;
   for(uint r=0;r<30;r++){
     for(uint k=0;k<32;k++)dst[r*32+k]=v[k];
     b2(v,64,v,64);
   }
   for(uint k=0;k<64;k++)dst[960+k]=v[k];
 }
}
__kernel void finish_arg(__global const uchar *memory,__global uchar *out){
 uint job=get_global_id(0);
 uchar input[1028],digest[32];
 w32(input,32);
 __global const uchar *src=memory+(size_t)job*8388608+8191*1024;
 for(uint i=0;i<1024;i++)input[i+4]=src[i];
 b2(input,1028,digest,32);
 for(uint i=0;i<32;i++)out[job*32+i]=digest[i];
}
