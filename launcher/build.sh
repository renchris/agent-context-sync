#!/bin/bash
# Build and sign agentsync-launcher as a minimal app bundle: <OUT_DIR>/AgentSyncLauncher.app.
#
# The bundle is what a LaunchAgent runs (ProgramArguments[0] = .../Contents/MacOS/agentsync-launcher), what a
# user approves once ("“agentsync-launcher” wants to access files managed by …"), and what an MDM PPPC
# SystemPolicyAllFiles payload pins by bundle id + designated requirement (C15 §3).
#
# Environment (all optional):
#   SIGN_IDENTITY  codesign identity. Default "-" (ad hoc: the designated requirement is a cdhash, so every
#                  rebuild is a new TCC subject). A corporate build sets
#                  SIGN_IDENTITY="Developer ID Application: <Org> (<TEAMID>)".
#   BUNDLE_ID      CFBundleIdentifier / code-signing identifier. Default com.agentsync.launcher.
#   OUT_DIR        where AgentSyncLauncher.app is written. Default <this dir>/build.
#   ARCHS          space-separated slices to build. Default "arm64 x86_64" (universal).
#   MIN_MACOS      deployment target. Default 12.0.
#   ALLOWED_PROGRAM  the ONLY program the launcher may start (absolute path of agentsync's interpreter, e.g.
#                  "$(uv tool dir)/agentsync/bin/python"), baked into the sealed Info.plist; the child must be
#                  that path followed by "-I -X utf8 -m agentsync sync". scripts/install.sh sets it.
#   ALLOW_ANY_PROGRAM=1  development/test builds only: no pin, any absolute program runs (a TCC grant on such
#                  a bundle is a grant for every same-user process). Ignored when ALLOWED_PROGRAM is set.
#
# Usage: build.sh [--print-source-sha256]   (the flag prints the hash install.sh compares, and builds nothing)
#
# Output: build log lines on stderr; on stdout exactly three "key: value" lines (app, source_sha256,
# designated_requirement) so scripts can parse them. Exit 69 (EX_UNAVAILABLE) when no developer tools are
# installed: this script never triggers the Command Line Tools install dialog.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
BUNDLE_ID="${BUNDLE_ID:-com.agentsync.launcher}"
OUT_DIR="${OUT_DIR:-$here/build}"
ARCHS="${ARCHS:-arm64 x86_64}"
MIN_MACOS="${MIN_MACOS:-12.0}"
ALLOWED_PROGRAM="${ALLOWED_PROGRAM:-}"
ALLOW_ANY_PROGRAM="${ALLOW_ANY_PROGRAM:-0}"
case "$ALLOWED_PROGRAM" in
"" | /*) ;;
*)
	printf 'build.sh: ALLOWED_PROGRAM must be an absolute path: %s\n' "$ALLOWED_PROGRAM" >&2
	exit 64
	;;
esac
VERSION="1.0.0"
APP_NAME="AgentSyncLauncher.app"
EXE_NAME="agentsync-launcher"

say() { printf 'build.sh: %s\n' "$*" >&2; }

source_sha256() {
	# Everything that determines the built bytes and the signature, so install.sh can skip an identical rebuild
	# (an ad-hoc rebuild would otherwise invalidate the user's TCC approval).
	{
		cat "$here/Sources/main.swift" "$here/Info.plist.in" "$here/build.sh"
		printf '%s\n' "$SIGN_IDENTITY" "$BUNDLE_ID" "$ARCHS" "$MIN_MACOS" "$VERSION" "$ALLOWED_PROGRAM" \
			"$ALLOW_ANY_PROGRAM"
	} | /usr/bin/shasum -a 256 | cut -d' ' -f1
}

case "${1:-}" in
--print-source-sha256)
	source_sha256
	exit 0
	;;
-h | --help)
	awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
	exit 0
	;;
"") ;;
*)
	say "unknown argument: $1 (usage: build.sh [--print-source-sha256])"
	exit 64
	;;
esac

# Never call swiftc/xcrun without developer tools: the /usr/bin shims would pop the CLT install dialog.
devdir="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
if [ -z "$devdir" ] || [ ! -d "$devdir" ]; then
	say "no Xcode or Command Line Tools (xcode-select -p is empty); use a prebuilt $APP_NAME"
	exit 69
fi
swiftc="$(/usr/bin/xcrun --find swiftc 2>/dev/null || true)"
if [ -z "$swiftc" ] || [ ! -x "$swiftc" ]; then
	say "swiftc not found under $devdir; use a prebuilt $APP_NAME"
	exit 69
fi
sdk="$(/usr/bin/xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
if [ -z "$sdk" ] || [ ! -d "$sdk" ]; then
	say "no macOS SDK under $devdir; use a prebuilt $APP_NAME"
	exit 69
fi

sha="$(source_sha256)"

mkdir -p "$OUT_DIR"
stage="$(mktemp -d "$OUT_DIR/.stage.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

slices=()
for arch in $ARCHS; do
	say "compiling $arch (macOS >= $MIN_MACOS)"
	"$swiftc" -O -swift-version 5 -sdk "$sdk" -target "$arch-apple-macos$MIN_MACOS" \
		-o "$stage/$EXE_NAME-$arch" "$here/Sources/main.swift"
	slices+=("$stage/$EXE_NAME-$arch")
done

app="$stage/$APP_NAME"
mkdir -p "$app/Contents/MacOS"
if [ "${#slices[@]}" -eq 1 ]; then
	cp "${slices[0]}" "$app/Contents/MacOS/$EXE_NAME"
else
	/usr/bin/lipo -create -output "$app/Contents/MacOS/$EXE_NAME" "${slices[@]}"
fi
chmod 755 "$app/Contents/MacOS/$EXE_NAME"
xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'; }
sed_escape() { printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'; }
allow_any="<false/>"
if [ -z "$ALLOWED_PROGRAM" ] && [ "$ALLOW_ANY_PROGRAM" = "1" ]; then
	allow_any="<true/>"
	say "ALLOW_ANY_PROGRAM=1: an UNPINNED development build (never grant it TCC access)"
elif [ -z "$ALLOWED_PROGRAM" ]; then
	say "no ALLOWED_PROGRAM: this launcher will refuse every child (set ALLOWED_PROGRAM=<python>)"
fi
program_xml="$(sed_escape "$(xml_escape "$ALLOWED_PROGRAM")")"
sed -e "s/@SOURCE_SHA256@/$sha/" -e "s/@BUNDLE_ID@/$BUNDLE_ID/" -e "s/@VERSION@/$VERSION/" \
	-e "s/@MIN_MACOS@/$MIN_MACOS/" -e "s|@ALLOWED_PROGRAM@|$program_xml|" \
	-e "s|@ALLOW_ANY_PROGRAM@|$allow_any|" "$here/Info.plist.in" >"$app/Contents/Info.plist"
/usr/bin/plutil -lint -s "$app/Contents/Info.plist"

timestamp_flag="--timestamp=none"
if [ "$SIGN_IDENTITY" != "-" ]; then
	timestamp_flag="--timestamp" # a Developer ID signature needs a secure timestamp for notarization
fi
say "signing with identity '$SIGN_IDENTITY', identifier $BUNDLE_ID, hardened runtime"
/usr/bin/codesign --force --sign "$SIGN_IDENTITY" --identifier "$BUNDLE_ID" --options runtime \
	"$timestamp_flag" "$app"
/usr/bin/codesign --verify --strict --verbose=1 "$app" >&2

dest="$OUT_DIR/$APP_NAME"
rm -rf "$dest.old"
if [ -e "$dest" ]; then mv "$dest" "$dest.old"; fi
mv "$app" "$dest"
rm -rf "$dest.old"

requirement="$(/usr/bin/codesign -d -r- "$dest" 2>&1 | sed -n 's/^#* *designated => //p')"
case "$requirement" in
cdhash* | "")
	say "ad-hoc signature: the designated requirement is a cdhash, so a rebuild is a new TCC subject and"
	say "PPPC cannot pin it; for a managed fleet rebuild with SIGN_IDENTITY='Developer ID Application: ...'"
	;;
*"certificate leaf[subject.OU]"*)
	say "PPPC SystemPolicyAllFiles: Identifier $BUNDLE_ID, IdentifierType bundleID, CodeRequirement below"
	;;
esac
printf 'app: %s\n' "$dest"
printf 'source_sha256: %s\n' "$sha"
printf 'designated_requirement: %s\n' "$requirement"
