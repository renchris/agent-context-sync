# Build the probes with the system toolchain. Absolute paths on purpose: some shells shadow `cc`.
CLANG  ?= /usr/bin/clang
SWIFTC ?= /usr/bin/swiftc
CFLAGS ?= -O2 -Wall -Wextra
BIN    := bin

all: $(BIN)/walkfp $(BIN)/readfp $(BIN)/gen1 $(BIN)/fswatch $(BIN)/evict

$(BIN):
	mkdir -p $(BIN)

$(BIN)/%: %.c | $(BIN)
	$(CLANG) $(CFLAGS) -o $@ $<

$(BIN)/%: %.swift | $(BIN)
	$(SWIFTC) -O -o $@ $<

clean:
	rm -f $(BIN)/walkfp $(BIN)/readfp $(BIN)/gen1 $(BIN)/fswatch $(BIN)/evict

# Smoke test on a throwaway local tree: exit codes, counts and the fail-closed paths (added 2026-09-29).
check: all
	/bin/bash smoke.sh $(CURDIR)/$(BIN)

.PHONY: all clean check
