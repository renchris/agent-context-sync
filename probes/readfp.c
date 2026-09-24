/* readfp <on|off|default> <path> — read a (possibly dataless) file under a chosen materialization policy.
 * Prints the policy in force, the file's flags and allocated blocks before and after, and the result of
 * open() and read() with errno. On a dataless File Provider file: policy ON downloads it, OFF fails the
 * read with EDEADLK (errno 11). "default" keeps whatever the calling context inherited, which is ON for
 * a login shell and OFF for a launchd job (measured, docs/design/receipts/verify/C14-file-provider.md §2). */
#include <sys/resource.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <time.h>
#ifndef SF_DATALESS
#define SF_DATALESS 0x40000000
#endif
static const char *polname(int p){ return p==IOPOL_MATERIALIZE_DATALESS_FILES_ON?"on":p==IOPOL_MATERIALIZE_DATALESS_FILES_OFF?"off":p==0?"default":"?"; }
static const char *state(unsigned f){ return (f&SF_DATALESS)?" (dataless)":""; }
int main(int c,char**v){
  if(c<3){ fprintf(stderr,"usage: readfp <on|off|default> <path>\n"); return 2; }
  if(!strcmp(v[1],"on")){ if(setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,IOPOL_SCOPE_PROCESS,IOPOL_MATERIALIZE_DATALESS_FILES_ON)){ perror("setiopolicy_np on"); return 3; } }
  if(!strcmp(v[1],"off")){ if(setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,IOPOL_SCOPE_PROCESS,IOPOL_MATERIALIZE_DATALESS_FILES_OFF)){ perror("setiopolicy_np off"); return 3; } }
  int pol=getiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,IOPOL_SCOPE_PROCESS);
  struct stat st; stat(v[2],&st);
  struct timespec a,b; clock_gettime(CLOCK_MONOTONIC,&a);
  int fd=open(v[2],O_RDONLY); int oe=errno; char buf[65536]; ssize_t n=-1, tot=0; int re=0;
  if(fd>=0){ while((n=read(fd,buf,sizeof buf))>0) tot+=n; re=(n<0)?errno:0; close(fd); }
  clock_gettime(CLOCK_MONOTONIC,&b); struct stat st2; stat(v[2],&st2);
  printf("policy=%s(%d)  before: flags=0x%x%s blocks=%lld\n", polname(pol), pol, st.st_flags, state(st.st_flags), (long long)st.st_blocks);
  int tty=isatty(STDOUT_FILENO); const char *red=tty?"\033[31m":"", *green=tty?"\033[32m":"", *off=tty?"\033[0m":"";
  if(fd<0) printf("%sopen=%s (errno %d)%s", red, strerror(oe), oe, off);
  else if(re) printf("open=ok  %sread=%s (errno %d)%s", red, strerror(re), re, off);
  else printf("open=ok  %sread=ok%s", green, off);
  printf("  bytes=%zd  %.3fs\n", tot, (b.tv_sec-a.tv_sec)+(b.tv_nsec-a.tv_nsec)/1e9);
  printf("after:  flags=0x%x%s blocks=%lld\n", st2.st_flags, state(st2.st_flags), (long long)st2.st_blocks);
  return 0; }
