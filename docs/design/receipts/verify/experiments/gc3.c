#include <sys/attr.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
int main(int argc,char**argv){
 for(int i=1;i<argc;i++){
  struct attrlist al; memset(&al,0,sizeof(al));
  al.bitmapcount=ATTR_BIT_MAP_COUNT;
  al.commonattr=ATTR_CMN_RETURNED_ATTRS|ATTR_CMN_FILEID|ATTR_CMN_MODTIME|ATTR_CMN_FLAGS|ATTR_CMN_GEN_COUNT|ATTR_CMN_DOCUMENT_ID;
  char buf[1024];
  if(getattrlist(argv[i],&al,buf,sizeof(buf),FSOPT_ATTR_CMN_EXTENDED|FSOPT_NOFOLLOW|FSOPT_PACK_INVAL_ATTRS)){printf("%s ERRNO=%d\n",argv[i],errno);continue;}
  char*p=buf+sizeof(uint32_t);
  attribute_set_t ret; memcpy(&ret,p,sizeof(ret)); p+=sizeof(ret);
  uint64_t fid=0; struct timespec mt={0,0}; uint32_t fl=0,gc=0,did=0;
  memcpy(&mt,p,sizeof(mt));p+=sizeof(mt);
  memcpy(&fl,p,4);p+=4;
  memcpy(&gc,p,4);p+=4;
  memcpy(&did,p,4);p+=4;
  /* CORRECTED (2026-09-29): a `p+=4; pad to 8-byte align for fileid` stood here. The kernel packs attributes
   * on 4-byte boundaries with no such pad (measured: record length 60, FILEID at offset 52 = stat's inode),
   * so gc3 read FILEID 4 bytes late and printed buffer garbage (e.g. 1152921500311879680, and the same value
   * for /dev/null). The pad is removed. FSOPT_PACK_INVAL_ATTRS keeps this fixed layout valid even when an
   * attribute is not returned (ret_common then lacks its bit and the slot is zero). gc4.c parsed correctly,
   * and the FILEID figures in the C2 receipt use gc4's format. */
  memcpy(&fid,p,8);p+=8;
  printf("%-10s ret_common=0x%x fileid=%llu mtime=%ld.%09ld flags=0x%x GEN=%u DOCID=%u dataless=%d\n",
    argv[i],ret.commonattr,fid,(long)mt.tv_sec,(long)mt.tv_nsec,fl,gc,did,(fl&SF_DATALESS)?1:0);
 }
 return 0;}
