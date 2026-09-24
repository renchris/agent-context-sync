#!/bin/bash
# record-demo.sh <onedrive-folder> — re-record docs/media/dataless-read.webp headlessly with VHS.
# The folder must be inside a OneDrive (File Provider) mount and hold a file named blob.bin; the tape
# evicts it, so its first read downloads it again. Needs: vhs, gif2webp (brew install vhs webp).
set -eu
[ $# -eq 1 ] && [ -f "$1/blob.bin" ] || { echo "usage: record-demo.sh <onedrive-folder-with-blob.bin>" >&2; exit 2; }
root=$(cd "$(dirname "$0")/.." && pwd)
make -C "$root/probes" >/dev/null
mkdir -p /tmp/acs-demo
ACS_DEMO_DIR=$(cd "$1" && pwd) ACS_PROBES="$root/probes" vhs "$root/docs/media/dataless-read.tape"
# Flat terminal output: gif2webp is lossless by default, and -min_size is what makes it smaller than the GIF.
gif2webp -m 6 -min_size -mt /tmp/acs-demo/dataless-read.gif -o "$root/docs/media/dataless-read.webp"
ls -l "$root/docs/media/dataless-read.webp"
