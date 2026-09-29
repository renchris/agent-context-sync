/* walkfp.c — C11 walker + per-entry report: is ATTR_CMN_GEN_COUNT returned, is the entry dataless.
 *
 * CORRECTED (2026-09-29): the walker used to fail open. With no argument, an unreadable root, a failed
 * getattrlistbulk or an unreadable subdirectory it printed a normal-looking (partial or zero) summary and
 * exited 0. It now prints usage and exits 2 without an argument, counts every open/getattrlistbulk/per-entry
 * (ATTR_CMN_ERROR) failure, appends "errors=N" to the summary and exits 1 when N > 0. The success-path
 * summary line is unchanged. Also corrected: each recursion level used to hold a 64 KiB buffer and a 4 KiB
 * path on the stack plus an open directory fd, so a tree about 120 levels deep overflowed the 8 MiB stack
 * (SIGSEGV). The buffer is now allocated once on the heap, and each directory is fully listed and closed
 * before its subdirectories are walked (so -v lists a directory's own entries before its subdirectories').
 * Fixed-size attribute fields are read with memcpy: getattrlistbulk packs them on 4-byte boundaries, and
 * the old uint64_t / uint32_t pointer casts were misaligned loads (undefined behaviour, flagged by UBSan). */
#include <sys/attr.h>
#include <sys/vnode.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifndef SF_DATALESS
#define SF_DATALESS 0x40000000
#endif
#define BUFSZ 65536
static long files=0, dirs=0, gen_ret=0, gen_nonzero=0, dataless=0, verbose=0, errors=0;
static char *buf;
static void fail(const char *what, const char *path, int err){ errors++; fprintf(stderr,"walkfp: %s(%s): %s\n",what,path,strerror(err)); }
static void walk(const char *path){
  int fd=open(path,O_RDONLY|O_DIRECTORY); if(fd<0){ fail("open",path,errno); return; } dirs++;
  struct attrlist al; memset(&al,0,sizeof al); al.bitmapcount=ATTR_BIT_MAP_COUNT;
  al.commonattr=ATTR_CMN_RETURNED_ATTRS|ATTR_CMN_NAME|ATTR_CMN_ERROR|ATTR_CMN_OBJTYPE|ATTR_CMN_MODTIME|ATTR_CMN_FILEID|ATTR_CMN_GEN_COUNT|ATTR_CMN_FLAGS;
  al.fileattr=ATTR_FILE_DATALENGTH;
  char **subs=NULL; size_t nsub=0, capsub=0;
  for(;;){ int n=getattrlistbulk(fd,&al,buf,BUFSZ,0); if(n<=0){ if(n<0) fail("getattrlistbulk",path,errno); break; }
    char *p=buf; for(int i=0;i<n;i++){ char *e=p; uint32_t len; memcpy(&len,e,sizeof len); char *q=e+4;
      if(len<4+sizeof(attribute_set_t) || e+len>buf+BUFSZ){ fail("getattrlistbulk",path,EBADMSG); goto done; }
      attribute_set_t ret; memcpy(&ret,q,sizeof ret); q+=sizeof(attribute_set_t);
      /* ATTR_CMN_ERROR, when returned, comes right after ATTR_CMN_RETURNED_ATTRS (getattrlistbulk(2)) */
      uint32_t eerr=0; if(ret.commonattr&ATTR_CMN_ERROR){ memcpy(&eerr,q,sizeof eerr); q+=sizeof(uint32_t); }
      char *name=NULL; if(ret.commonattr&ATTR_CMN_NAME){ attrreference_t ar; memcpy(&ar,q,sizeof ar); name=q+ar.attr_dataoffset; q+=sizeof(attrreference_t);}
      if(eerr){ char ep[4096]; snprintf(ep,sizeof ep,"%s/%s",path,name?name:"?"); fail("entry",ep,(int)eerr); p=e+len; continue; }
      fsobj_type_t ot=VNON; if(ret.commonattr&ATTR_CMN_OBJTYPE){ memcpy(&ot,q,sizeof ot); q+=sizeof(fsobj_type_t);}
      if(ret.commonattr&ATTR_CMN_MODTIME) q+=sizeof(struct timespec);
      /* buffer order is attribute BIT order: FLAGS 0x40000, GEN_COUNT 0x80000, FILEID 0x2000000 */
      uint32_t fl=0; if(ret.commonattr&ATTR_CMN_FLAGS){ memcpy(&fl,q,sizeof fl); q+=sizeof(uint32_t);}
      uint32_t gen=0; int hasgen=0; if(ret.commonattr&ATTR_CMN_GEN_COUNT){ memcpy(&gen,q,sizeof gen); q+=sizeof(uint32_t); hasgen=1; }
      uint64_t fid=0; if(ret.commonattr&ATTR_CMN_FILEID){ memcpy(&fid,q,sizeof fid); q+=sizeof(uint64_t);}
      if(ot!=VDIR){ files++; gen_ret+=hasgen; gen_nonzero+=(hasgen&&gen!=0); dataless+=((fl&SF_DATALESS)!=0);
        if(verbose) printf("  %s/%s fileid=%llu gen_returned=%d gen=%u flags=0x%x%s\n",path,name?name:"?",(unsigned long long)fid,hasgen,gen,fl,(fl&SF_DATALESS)?" DATALESS":""); }
      if(ot==VDIR && name){
        char *sub=NULL; if(asprintf(&sub,"%s/%s",path,name)<0 || !sub){ fail("asprintf",path,ENOMEM); p=e+len; continue; }
        if(strlen(sub)>=PATH_MAX){ fail("path",sub,ENAMETOOLONG); free(sub); p=e+len; continue; }
        if(nsub==capsub){ size_t nc=capsub?capsub*2:16; char **ns=realloc(subs,nc*sizeof *ns); if(!ns){ fail("realloc",path,ENOMEM); free(sub); p=e+len; continue; } subs=ns; capsub=nc; }
        subs[nsub++]=sub; }
      p=e+len; } }
done:
  close(fd);
  for(size_t i=0;i<nsub;i++){ walk(subs[i]); free(subs[i]); }
  free(subs);
}
int main(int c,char**v){
  if(c<2 || c>3){ fprintf(stderr,"usage: walkfp <dir> [-v]\n"); return 2; }
  buf=malloc(BUFSZ); if(!buf){ perror("malloc"); return 1; }
  struct timespec a,b; if(c>2) verbose=1; clock_gettime(CLOCK_MONOTONIC,&a); walk(v[1]); clock_gettime(CLOCK_MONOTONIC,&b);
  printf("files=%ld dirs=%ld gen_count_returned=%ld gen_count_nonzero=%ld dataless=%ld wall=%.3fs",files,dirs,gen_ret,gen_nonzero,dataless,(b.tv_sec-a.tv_sec)+(b.tv_nsec-a.tv_nsec)/1e9);
  if(errors) printf(" errors=%ld INCOMPLETE",errors);
  printf("\n"); free(buf); return errors?1:0; }
