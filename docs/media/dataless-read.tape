# VHS tape for docs/media/dataless-read.webp. Run it through scripts/record-demo.sh, which sets
# ACS_DEMO_DIR (a OneDrive folder holding a 2 MB blob.bin) and ACS_PROBES (probes/ in this repo).
# The setup is hidden so no local path appears in any frame.

Output "/tmp/acs-demo/dataless-read.gif"

Set Shell bash
Set FontFamily "Menlo"
Set FontSize 17
Set Width 1060
Set Height 530
Set Padding 28
Set LineHeight 1.25
Set TypingSpeed 28ms
Set Theme { "name": "acs", "background": "#0d1117", "foreground": "#e6edf3", "cursor": "#8b949e", "selection": "#3d444d", "black": "#0d1117", "red": "#ff7b72", "green": "#3fb950", "yellow": "#f0a33a", "blue": "#79c0ff", "magenta": "#d2a8ff", "cyan": "#76e3ea", "white": "#e6edf3", "brightBlack": "#8b949e", "brightRed": "#ffa198", "brightGreen": "#56d364", "brightYellow": "#f0a33a", "brightBlue": "#a5d6ff", "brightMagenta": "#e2c5ff", "brightCyan": "#b3f0ff", "brightWhite": "#ffffff" }

Hide
Type `export PS1='\[\e[90m\]$\[\e[0m\] ' PATH="$ACS_PROBES/bin:$ACS_PROBES:$PATH"; cd "$ACS_DEMO_DIR" && clear`
Enter
Sleep 800ms
Show

Type "evict blob.bin                        # make a OneDrive file online-only"
Sleep 300ms
Enter
Sleep 1s
Type "stat -f '%Sf  %z bytes  %b blocks' blob.bin"
Sleep 300ms
Enter
Sleep 2s

Type "launchd-run.sh readfp default /dev/null   # a launchd job's default policy"
Sleep 300ms
Enter
Sleep 3s

Type "readfp off blob.bin                   # that policy, on the placeholder"
Sleep 300ms
Enter
Sleep 3500ms

Type "readfp default blob.bin               # a login shell's default: on"
Sleep 300ms
Enter
Sleep 5s
