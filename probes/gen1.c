#include <sys/attr.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
/* CORRECTED (2026-09-29): with no argument gen1 used to call getattrlist(NULL) ("Bad address"); it now prints
 * usage and exits 2. The packed struct below matches the kernel's 4-byte packing (FILEID at offset 16). */
int main(int c,char**v){ if(c!=2){ fprintf(stderr,"usage: gen1 <path>\n"); return 2; } struct attrlist al; memset(&al,0,sizeof al); al.bitmapcount=ATTR_BIT_MAP_COUNT; al.commonattr=ATTR_CMN_RETURNED_ATTRS|ATTR_CMN_FLAGS|ATTR_CMN_GEN_COUNT|ATTR_CMN_FILEID;
 struct { uint32_t len; attribute_set_t ret; uint32_t fl; uint32_t gen; uint64_t fid; } __attribute__((packed)) b;
 if(getattrlist(v[1],&al,&b,sizeof b,FSOPT_PACK_INVAL_ATTRS|FSOPT_ATTR_CMN_EXTENDED)) { perror("getattrlist"); return 1; }
 printf("fileid=%llu gen=%u flags=0x%x gen_returned=%d\n",(unsigned long long)b.fid,b.gen,b.fl,!!(b.ret.commonattr&ATTR_CMN_GEN_COUNT)); return 0; }
