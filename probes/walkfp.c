/* walkfp.c — C11 walker + per-entry report: is ATTR_CMN_GEN_COUNT returned, is the entry dataless. */
#include <sys/attr.h>
#include <sys/vnode.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#ifndef SF_DATALESS
#define SF_DATALESS 0x40000000
#endif
static long files=0, dirs=0, gen_ret=0, gen_nonzero=0, dataless=0, verbose=0;
static void walk(const char *path){
  int fd=open(path,O_RDONLY|O_DIRECTORY); if(fd<0){ perror(path); return; } dirs++;
  struct attrlist al; memset(&al,0,sizeof al); al.bitmapcount=ATTR_BIT_MAP_COUNT;
  al.commonattr=ATTR_CMN_RETURNED_ATTRS|ATTR_CMN_NAME|ATTR_CMN_OBJTYPE|ATTR_CMN_MODTIME|ATTR_CMN_FILEID|ATTR_CMN_GEN_COUNT|ATTR_CMN_FLAGS;
  al.fileattr=ATTR_FILE_DATALENGTH;
  char buf[65536];
  for(;;){ int n=getattrlistbulk(fd,&al,buf,sizeof buf,0); if(n<=0){ if(n<0){ fprintf(stderr,"getattrlistbulk(%s): ",path); perror(""); } break; }
    char *p=buf; for(int i=0;i<n;i++){ char *e=p; uint32_t len=*(uint32_t*)e; char *q=e+4;
      attribute_set_t ret=*(attribute_set_t*)q; q+=sizeof(attribute_set_t);
      char *name=NULL; if(ret.commonattr&ATTR_CMN_NAME){ attrreference_t *ar=(attrreference_t*)q; name=(char*)ar+ar->attr_dataoffset; q+=sizeof(attrreference_t);}
      fsobj_type_t ot=VNON; if(ret.commonattr&ATTR_CMN_OBJTYPE){ ot=*(fsobj_type_t*)q; q+=sizeof(fsobj_type_t);}
      if(ret.commonattr&ATTR_CMN_MODTIME) q+=sizeof(struct timespec);
      /* buffer order is attribute BIT order: FLAGS 0x40000, GEN_COUNT 0x80000, FILEID 0x2000000 */
      uint32_t fl=0; if(ret.commonattr&ATTR_CMN_FLAGS){ fl=*(uint32_t*)q; q+=sizeof(uint32_t);}
      uint32_t gen=0; int hasgen=0; if(ret.commonattr&ATTR_CMN_GEN_COUNT){ gen=*(uint32_t*)q; q+=sizeof(uint32_t); hasgen=1; }
      uint64_t fid=0; if(ret.commonattr&ATTR_CMN_FILEID){ fid=*(uint64_t*)q; q+=sizeof(uint64_t);}
      if(ot!=VDIR){ files++; gen_ret+=hasgen; gen_nonzero+=(hasgen&&gen!=0); dataless+=((fl&SF_DATALESS)!=0);
        if(verbose) printf("  %s/%s fileid=%llu gen_returned=%d gen=%u flags=0x%x%s\n",path,name?name:"?",(unsigned long long)fid,hasgen,gen,fl,(fl&SF_DATALESS)?" DATALESS":""); }
      if(ot==VDIR && name){ char sub[4096]; snprintf(sub,sizeof sub,"%s/%s",path,name); walk(sub);}
      p=e+len; } }
  close(fd);
}
int main(int c,char**v){ struct timespec a,b; if(c>2) verbose=1; clock_gettime(CLOCK_MONOTONIC,&a); walk(v[1]); clock_gettime(CLOCK_MONOTONIC,&b);
  printf("files=%ld dirs=%ld gen_count_returned=%ld gen_count_nonzero=%ld dataless=%ld wall=%.3fs\n",files,dirs,gen_ret,gen_nonzero,dataless,(b.tv_sec-a.tv_sec)+(b.tv_nsec-a.tv_nsec)/1e9); return 0; }
