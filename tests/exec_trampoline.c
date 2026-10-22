/* exec_trampoline: the Mach-O a test stub is, so a stub run by path is no #! exec (B13 round 23 lane K, A97).
 *
 * macOS 15.8.1 keeps about 1.1 KiB of kernel memory for every #! script run by path, until reboot, and the
 * suite's stubs ran thousands of times a minute. tests/exec_stub.py writes each stub as a copy of this binary
 * with the absolute path of its body (the #! script the test wrote) appended:
 *
 *     <this binary> <body path> <length of the path, 4 bytes little endian> "XTRAMP01"
 *
 * Run by path, it reads that trailer back from its own file and execs the interpreter the body's first line
 * names, with the body's path as the script argument, the way the kernel would (xnu bsd/kern/kern_exec.c
 * exec_shell_imgact: at most 512 bytes, space and tab separate the words, a newline or # ends the line, a CR is
 * part of the last word). A body without #! runs under /bin/sh, as execvp does. It never forks: the pid, the
 * descriptors, the signal mask, the environment and the exit status are the interpreter's own. The bytes past
 * the code signature are not signed and not mapped, so every copy keeps the one signature cc made.
 */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAGIC "XTRAMP01"
#define TRAILER 12 /* the length and the magic */
#define SHSIZE 512 /* IMG_SHSIZE */

static void die(int code, const char *what, const char *why) {
	fprintf(stderr, "exec-trampoline: %s: %s\n", what, why);
	exit(code);
}

int main(int argc, char **argv, char **envp, char **apple) {
	const char *self = NULL;
	static char found[PATH_MAX];
	for (char **a = apple; a && *a; a++) {
		if (strncmp(*a, "executable_path=", 16) == 0) {
			self = *a + 16; /* the path execve was given: a copy is read where it is */
			break;
		}
	}
	if (self == NULL) {
		uint32_t size = sizeof found;
		if (_NSGetExecutablePath(found, &size) != 0)
			die(127, "its own path", "unknown");
		self = found;
	}

	int fd = open(self, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		die(127, self, strerror(errno));
	struct stat st;
	unsigned char tail[TRAILER];
	if (fstat(fd, &st) != 0 || st.st_size < TRAILER ||
	    pread(fd, tail, TRAILER, st.st_size - TRAILER) != TRAILER || memcmp(tail + 4, MAGIC, 8) != 0)
		die(127, self, "no stub body recorded (tests/exec_stub.py writes one)");
	uint32_t len = (uint32_t)tail[0] | (uint32_t)tail[1] << 8 | (uint32_t)tail[2] << 16 | (uint32_t)tail[3] << 24;
	static char body[PATH_MAX];
	if (len == 0 || len >= sizeof body || (off_t)len + TRAILER > st.st_size ||
	    pread(fd, body, len, st.st_size - TRAILER - len) != (ssize_t)len)
		die(127, self, "the stub body's path is cut short");
	body[len] = '\0';
	close(fd);

	static char line[SHSIZE + 1];
	int bfd = open(body, O_RDONLY | O_CLOEXEC);
	if (bfd < 0)
		die(127, body, strerror(errno));
	ssize_t n = read(bfd, line, SHSIZE);
	close(bfd);
	if (n < 0)
		die(126, body, strerror(errno));
	line[n] = '\0';

	char *word[SHSIZE / 2 + 2];
	int k = 0;
	if (n >= 2 && line[0] == '#' && line[1] == '!') {
		char *end = NULL;
		for (char *q = line + 2; q < line + n; q++) {
			if (*q == '\n' || *q == '#') {
				end = q;
				break;
			}
		}
		if (end == NULL)
			die(126, body, "no end to the #! line in its first 512 bytes");
		*end = '\0';
		for (char *q = line + 2; *q;) {
			while (*q == ' ' || *q == '\t')
				*q++ = '\0';
			if (*q == '\0')
				break;
			word[k++] = q;
			while (*q && *q != ' ' && *q != '\t')
				q++;
		}
		if (k == 0)
			die(126, body, "the #! line names no interpreter");
	} else {
		word[k++] = "/bin/sh";
	}

	char **args = calloc((size_t)k + (size_t)argc + 1, sizeof *args);
	if (args == NULL)
		die(126, self, strerror(errno));
	int j = 0;
	for (int i = 0; i < k; i++)
		args[j++] = word[i];
	args[j++] = body;
	for (int i = 1; i < argc; i++)
		args[j++] = argv[i];
	args[j] = NULL;
	execve(word[0], args, envp);
	die(errno == ENOENT ? 127 : 126, word[0], strerror(errno));
	return 126;
}
