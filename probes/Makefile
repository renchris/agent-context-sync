# Build the probes with the system toolchain. Absolute paths on purpose: some shells shadow `cc`.
CLANG  ?= /usr/bin/clang
SWIFTC ?= /usr/bin/swiftc
CFLAGS ?= -O2 -Wall
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

.PHONY: all clean
