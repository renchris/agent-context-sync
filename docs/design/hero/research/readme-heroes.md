# README heroes on github.com: what autoplays, how big, and what the first 3 s do

Surveyed 2026-09-24. 134 READMEs fetched with `gh api repos/<o>/<r>/readme` (136 requested; 2 empty). 20 animated or video heroes and 17 static ones from the brief's list were measured. Every asset was downloaded with `curl -sL` and measured locally. Rendering was checked logged-out in headless Chrome through `agent-browser` at a 1280×1400 viewport.

**How numbers are labelled.** Numbers marked *measured* name their tool:
- bytes: `curl -w %{size_download}`, cross-checked with ffprobe `format=size`
- dimensions, frames, duration, fps: `ffprobe -count_frames`
- per-frame GIF delays: `magick identify -ping -format %T`
- loop count: the GIF NETSCAPE2.0 block or the APNG acTL chunk, read in Python
- WebP animation flag: `webpinfo -summary`
- "first change": a numpy diff against frame 0 on ffmpeg 20 fps grayscale frames, where the first sample with at least 0.5% of pixels changed by more than 25/255 counts as the first change
- DOM facts: `agent-browser eval`

The "t=0 states value?" column and the verdicts are my judgement from frames extracted at 0, 1 and 3 s. Display sizes come from the rendered DOM where I opened the page, and otherwise from the authored `width` attribute capped at GitHub's 838 px README column (the column width was measured).

Assets, frames and screenshots are under `../readme-heroes/`. Downloaded assets are in `assets/`. `frames/*_sheet.jpg` holds the 0/1/3 s contact sheets, and `frames/*_display*.png` holds the same three frames at the size GitHub actually shows them. Screenshots are in the top level.

Repo renames I followed: calcom/cal.com now serves calcom/cal.diy, sst/opencode is anomalyco/opencode, and yetone/avante.nvim is avante-corp/avante.nvim.

## 1. Animated and video heroes (measured)

"Shown" is the width on github.com at a 1280 px viewport. The README column is 838 px.

| repo | medium (hosting) | bytes | WxH native → shown | duration | fps (frames) | light/dark? | t=0 states value? | verdict |
|---|---|---|---|---|---|---|---|---|
| anthropics/claude-code | GIF, relative repo file `./demo.gif` (served from `/raw/main/`) | 11,002,760 | 1552×992 → 838×536 | 42.4 s, loops | 10 (414) | no | Partly. Header reads "Claude Code v2.0.0" and the prompt "audit and improve test coverage" is already typed. No claim. | Highest production value. macOS window on a gradient wallpaper, a keycap overlay (↵) at 2.6 s, then a real task end to end. Heaviest measured, and only possible as a repo file because it is over the 10 MB upload cap. |
| charmbracelet/crush | GIF, user-attachments | 8,577,156 | 1600×900 → 800×450 | 6.21 s | 24–25 (149; 4/5 cs delays) | no | No. Frame 0 is the middle of a bug-fix prompt. Brand shows only as the sidebar wordmark. | Great brand colour. Text shows at 0.5× capture size and is hard to read. At 1.38 MB/s it has the worst bytes per second measured. The 6 s loop cuts off mid-task. |
| calcom/cal.com | GIF (file named .png), legacy repo-assets upload | 4,650,099 | 800×492 → 838×515 (upscaled) | 12.0 s | 25 (300) | no | Shows it rather than saying it. The booking widget and "Cal.com" wordmark are on screen at t=0. | Clean product-UI demo with a real cursor. Click at 0.7 s; calendar → time slot → form by 3 s. Slightly soft from upscaling. |
| charmbracelet/vhs | GIF, CDN `stuff.charm.sh` → camo | 137,479 | 1300×650 → 600×300 | 8.64 s | 50 (432) | logo only (`#gh-dark-mode-only`) | No. Empty prompt `> ▌`. | Most byte-efficient terminal hero at 16 KB/s. neofetch output in brand colours at 0.95 s, then "Welcome to VHS!". |
| charmbracelet/gum | GIF, CDN `vhs.charm.sh` → camo | 183,635 | 800×450 → 600×338 | 30.4 s | 25 (760) | no | No. Just `> .`. | By 1 s a pink box reads "Hello, there! Welcome to Gum." Costs 6 KB/s. |
| atuinsh/atuin | GIF, relative repo file | 372,559 | 1771×1272 → 670 wide (80%) | 12.29 s | variable: 63 frames, delays 6–40 cs (avg 5.1/s) | logo only | No. Blank terminal. | Weak open: nothing happens for 2.5 s. The payoff (full-screen history search) lands at about 3 s. Text is shown at 0.38× and is tiny. |
| jesseduffield/lazygit | GIF via `../assets/` link to an orphan `assets` branch | 472,611 | 1140×828 → 838×609 | 14.15 s | variable: 67 frames (avg 4.7/s) | no | No. Frame 0 is a black window titled "Lazygit". From 0.1 s a caption bar reads "Stage a file Pressing <space>". | Its per-action captions are the best idea here. It sits below a sponsor block (README line 50). |
| vercel/ai | GIF, absolute `github.com/vercel/ai/blob/HEAD/assets/hero.gif` | 1,126,481 | 2166×546 → 838×211 | 24.0 s | 10 (240) | no (black grid reads in both themes) | **Yes**: "Integrate any model provider with a single API". | The only hero whose frame 0 is a finished value sentence. It holds 3.2 s, then retypes through three claims in a chat bubble. Two colours give 47 KB/s. It is also the README's first line. |
| charmbracelet/glow | two GIFs: banner via CDN → camo, then demo via user-attachments | 2,653,701 + 4,937,101 | 1200×270 → 838×189; 1200×675 → 800×450 | 3.03 s; 7.5 s | 30–33 (91); 50 (375) | no | No. Wordmark "charm_ glow" only (the tagline is markdown text above it). | A 3 s shimmering-logo loop, then a UI demo. The logo alone weighs as much as 19 VHS GIFs. |
| charmbracelet/bubbletea | GIF, CDN → camo | 897,531 | 1300×530 → 838×342 | 12.0 s | 50 (600) | no | No. Empty prompt. | Checklist UI by 3 s. Nothing distinctive. |
| zellij-org/zellij | GIF, absolute raw.githubusercontent.com (served directly, not camo) | 4,843,386 | 825×435 → 825×435 | 67.9 s | 10 (679) | no | No. Empty pane. | Longest loop measured. Small type, slow story. |
| remotion-dev/remotion | APNG (dark) and GIF (light) in `<picture>` | 725,249 / 276,490 | 1080×500 → 838×388 | 2.0 s, **plays once** | 30 (60) | **yes**, the only animated hero with variants | No. Blank at t=0; wordmark by 1 s. | A logo reveal that stops on the wordmark, the only hero that ends still. Says the name, not the value. |
| Aider-AI/aider | animated SVG (termtosvg CSS keyframes), `aider.chat` → camo | 118,106 | viewBox 687×500 → 687 wide | 15.54 s | 132 keyframes, variable (avg 8.5/s) | no | No. Blank cursor; `aider demo.py` typed by 1 s. | Crisp vector text at any width for 118 KB. Terminal-only technique. |
| httpie/cli | GIF, absolute raw.githubusercontent.com | 1,043,348 | 1024×512 → 838×419 | 19.24 s | 25 (481) | no | No. Empty prompt; "http" appears at 3 s. | Slow open: the first change comes at 1.95 s. |
| magicuidesign/magicui | GIF, CDN → camo | 3,204,675 | 640×360 → 838×471 (1.31× upscale) | 6.15 s | 20 (123) | no (light only) | Partly. Cards read "Save your files" and "Full text search", but that is demo copy. | Low-res and blurry after upscaling. 521 KB/s. |
| streamlit/streamlit | GIF, legacy user-images upload | 5,075,628 | 960×742 → 500×386 (`width=500`) | 12.6 s | 33 (416) | no | Yes on paper: "Welcome to Streamlit! … app framework built specifically for Machine Learning and Data Science". | At 0.52× the body copy can't be read, so the words don't land. |
| charmbracelet/mods | GIF, CDN → camo | 540,282 | 1600×900 → 838×471 | 22.8 s | 25 (570) | no | No. Empty prompt. | A 3 s `curl …` command is typed before anything happens. |
| sxyazi/yazi | MP4 (H.264 High + AAC), bare repo-assets URL → **click-to-play player** | 10,152,461 | 2040×1490 | 57.4 s | 30 (1723) | no | No. File-manager UI; a caption "Scrollable Preview" appears at about 1 s. | Best-edited video (feature captions), but it sits paused. It is below a sponsor table and the feature list. |
| yetone/avante.nvim | MP4 (H.264), user-attachments bare `<url>` → **click-to-play** | 19,373,585 | 2584×2160 → 764×639 (height-capped) | 43.65 s | 60 (2619) | no | No. Code in vim. | Screen-Studio-style camera zoom at 2.65 s plus a keycap overlay. It doesn't autoplay, and the tall aspect is capped at 640 px high. |
| coder/claudecode.nvim | MP4 (H.264), user-attachments → **click-to-play** | 27,752,803 | 3448×2160 | 29.7 s | 60 | no | No. Empty nvim buffer. | 7.5 Mb/s of 4K-class capture for an 838 px slot. Paused on an empty editor. |

In the table, "fps" is the GIF delay-derived rate: `avg_frame_rate` from ffprobe, and "variable" where per-frame delays differ. All GIFs loop forever (NETSCAPE loop = 0) except remotion-light.gif, which has no loop block. The remotion APNG has `num_plays = 1`.

**Frame 0 tally, from my frame reading:**
- 1 of 20 states the value legibly (vercel/ai).
- 1 does so illegibly (streamlit).
- 6 show the product without a claim: claude-code, cal.com, crush, glow, yazi, avante.
- 1 shows demo copy rather than the tool's value (magicui).
- 11 open on a blank screen or empty prompt: vhs, gum, atuin, lazygit, bubbletea, zellij, remotion, aider, httpie, mods, claudecode.nvim.

### Listed candidates whose hero is static or absent (measured bytes and dimensions; judgement from a montage, `frames/static-heroes-montage.jpg`)

| repo | medium | bytes | WxH | duration | fps | light/dark? | t=0 states value? | verdict |
|---|---|---|---|---|---|---|---|---|
| zed-industries/zed | none (badges and text) | – | – | – | – | – | – | no visual at all |
| warpdotdev/Warp | PNG, user-attachments | 919,513 | 1448×828 | static | – | no | Shows it: wordmark over the app window. | polished still |
| ghostty-org/ghostty | PNG logo, user-attachments | 221,047 | 512×512 (shown 128) | static | – | no | name only | logo only |
| astral-sh/uv | SVG benchmark chart, `<picture>` | 9,464 | vector | static | – | yes | Yes, in data: a speed bar chart. | the claim *is* the hero |
| astral-sh/ruff | SVG benchmark chart, `<picture>` | 9,227 | vector | static | – | yes | Yes, in data. | same pattern as uv |
| tldraw/tldraw | PNG, `<picture>` | 42,384 | 2560×1280 | static | – | yes | Yes: "tldraw Developer SDK". | wordmark plus category |
| excalidraw/excalidraw | PNG, `<picture>`, CDN → camo | 89,046 | 1280×336 | static | – | yes | name only | brand banner |
| oven-sh/bun | PNG logo | 22,914 | 412×347 | static | – | no | name only | logo |
| Textualize/textual | PNG splash, user-attachments | 370,654 | 1280×640 | static | – | no | Shows it: collage of TUI apps. | good still |
| Textualize/rich | SVG logo, then PNG feature sheet | 735,993 | 1988×2228 | static | – | no | shows features | wall of examples |
| sst/opencode | SVG logo `<picture>`, then PNG screenshot | 470,646 | 1824×1488 | static | – | logo yes | partly | screenshot |
| openai/codex | PNG splash | 838,131 | 1898×1190 | static | – | no | partly (a TUI session) | screenshot on purple |
| google-gemini/gemini-cli | PNG, relative | 61,239 | 1089×582 | static | – | no | Partly: big "GEMINI" wordmark. | screenshot |
| raycast/extensions | WebP, `webpinfo`: Animation 0 | 104,442 | 2048×1440 | static | – | no | shows the launcher | still |
| supabase/supabase | PNG logo `#gh-*-mode-only`, then dashboard PNG | 55,146 | 1440×1007 | static | – | logo yes | shows the dashboard | screenshot |
| n8n-io/n8n | PNG banner, then screenshot | 100,776 | 2880×1670 | static | – | no | shows the canvas | screenshot |
| motiondivision/motion | PNG logo shown at 35 px | 2,853 | 140×140 | static | – | no | no | An animation library with a static 35 px logo. |

## 2. What the best do in the first 3 s

Measured "first change" times:

| hero | first change |
|---|---|
| claude-code | 2.6 s |
| crush | 0.25 s |
| vercel/ai | 3.2 s |
| cal.com | 0.7 s |
| vhs | 0.95 s |
| gum | 0.45 s |
| atuin | 2.5 s |
| lazygit | 0.1 s |
| httpie | 1.95 s |
| bubbletea | 1.2 s |
| zellij | 1.2 s |
| mods | 0.9 s |
| yazi | 0.25 s |
| avante | 2.25 s |

1. **Frame 0 is already a finished frame.** claude-code opens with the prompt typed and the product header on screen. cal.com opens on a rendered booking page, and vercel/ai on a complete sentence. The weak half opens on `> ▌` (11 of 20).
2. **Hold, then show one deliberate change.** claude-code holds 2.6 s before an on-screen ↵ keycap fires the task. vercel/ai holds its claim for 3.2 s. The strongest openers do not move constantly. They present a readable state, then trigger one visible cause.
3. **They say what just happened.** lazygit has a caption bar ("Stage a file Pressing <space>", "Commit our changes"). yazi overlays feature titles ("Scrollable Preview") at about 1 s. claude-code and avante show keycap overlays. Captions are the cheapest way to make a silent loop explain itself.
4. **Brand stays in frame.** crush keeps its sidebar wordmark, cal.com keeps the wordmark under the widget, and claude-code keeps the mascot and name in the header. A viewer landing mid-loop still knows whose tool it is.
5. **A framed stage, not a raw capture.** claude-code and avante put the window on a wallpaper with padding and a shadow. vercel/ai uses a black grid and cal.com a dark void. This reads in both GitHub themes without `<picture>` variants.
6. **Honest production-value ranking** (from the display-size frames `frames/*_display*.png`):
   - claude-code: polished, legible at 0.54×.
   - vercel/ai: typographic, legible, but says the claim and nothing else.
   - cal.com: clean, slightly soft.
   - crush: beautiful but illegible at 0.5×, and too heavy.
   - vhs: plain, but a 137 KB masterclass in efficiency.
   - avante and yazi have the best editing (zoom, captions), and neither plays.

## 3. Size and duration ceiling in practice

Set: the 17 autoplaying assets (GIF, APNG, SVG) measured above, one primary per repo. Glow counts the demo GIF; remotion counts the dark APNG.

- **Bytes:** median **1,043,348** (≈1.0 MB), quartiles 0.42 / 1.04 / 4.89 MB, max **11,002,760** (claude-code, a committed repo file), min 118,106 (aider SVG). The largest *uploaded* one is crush at 8,577,156, just under GitHub's documented 10 MB cap for images and GIFs.
- **Duration:** median **12.6 s**, quartiles 8.1 / 12.6 / 23.4 s, max 67.9 s (zellij), min 2.0 s (remotion, plays once).
- **Bytes per second:** median 71 KB/s. The spread is set by content, not length:
  - text-only terminal (VHS, termtosvg): 6–24 KB/s
  - flat two-colour type: 47 KB/s
  - variable-delay terminal: 30–33 KB/s
  - UI or gradients at 20–33 fps: 388–521 KB/s
  - crush: 1,381 KB/s
- **Click-to-play videos** (not autoplaying) measured 10.2–27.8 MB, 29.7–57.4 s, 30–60 fps. They are bigger because they never have to autoplay.
- **Documented hosting caps** (docs.github.com, "Attaching files"): 10 MB for uploaded images and GIFs. Videos: 10 MB on free plans, 100 MB on paid. `.mp4`, `.mov` and `.webm` are accepted, with H.264 recommended. Committed repo files are not subject to the upload cap (claude-code's 11.0 MB GIF renders).
- **Proxying** (measured in the DOM):
  - Relative paths and absolute `raw.githubusercontent.com` URLs are served directly.
  - External CDNs go through `camo.githubusercontent.com`. A 2.65 MB GIF loaded fine; I did not find camo's upper limit.
  - user-attachments and repo-assets URLs become `private-user-images.githubusercontent.com` URLs signed with a 300 s JWT.
- **Where the hero sits** (measured, logged-out repo home page, 1280×1400): the README column starts 1,143–2,537 px down because the file list comes first. The hero's top was at 1,380 (claude-code), 1,537 (glow), 1,856 (crush), 2,062 (zellij), 2,413 (avante), 2,898 (reflex) and 2,986 px (vhs), a median of 2,062 px. None is fully above the fold.
- **The GIF clock runs from page load, not from scroll-in** (measured once, headless Chrome): I loaded claude-code, waited 9 s without scrolling, then scrolled to the hero. It showed "Grooving… · 7s · ↓ 511 tokens", which is the GIF's own t≈10 s frame. Visitors usually land mid-loop. `claude-code-after-9s.png`, `frames/claude-code-t5-12.jpg`.

## 4. How a user-attachments video renders

GitHub rewrites every video embed into the same widget. The markup is an open `<details class="details-reset border rounded-2">`. Its `<summary>` header shows a camera octicon plus the **uploaded filename** ("avante-2.mp4 ▾", "reflex-dalle-video-2x.mp4 ▾"). Below it sits `<video class="d-block rounded-bottom-2 border-top width-fit" style="max-height:640px; min-height:200px" controls muted>`.

- **Autoplay:** none.
- **Loop:** none.
- **Poster:** none. The player shows the video's own frame 0 because `preload=metadata`.
- **Controls and size:** native controls read "0:00 / 0:43" with a muted icon, and the player fills the 836 px card width unless the height cap bites.

Screenshots: `avante-inline-video.png` and `reflex-inline-video.png` (both in `../readme-heroes/`).

**avante.nvim** (bare `<https://github.com/user-attachments/assets/…>` line). Output of `agent-browser eval` on `article video`, first element, JWT elided:

```json
{"src":"https://private-user-images.githubusercontent.com/1206493/357962425-510e6270-….mp4?jwt=…",
 "autoplay":false,"muted":true,"loop":false,"controls":true,"poster":"",
 "w":764,"h":639,"preload":"metadata","paused":true,"currentTime":0,"readyState":4,
 "videoW":2584,"videoH":2160}
```

Attributes present: `src, data-canonical-src, controls, muted, class, style`. Wrapper: `DETAILS.details-reset.border.rounded-2`, 836×680. JWT lifetime (exp − nbf) is 300 s. The second video on the page reported the same flags (2640×2160, 781×639).

**reflex** (authored as `<video src="…user-attachments…" width="900" controls muted poster="…png">` with a fallback `<a><img></a>` inside):

```json
{"src":"https://private-user-images.githubusercontent.com/38776361/592684234-aaff28ad-….mp4?...",
 "autoplay":false,"muted":true,"loop":false,"controls":true,"poster":"",
 "w":836,"h":479,"preload":"metadata","paused":true,"currentTime":0,"readyState":4,
 "videoW":2512,"videoH":1440,"attrs":"src,data-canonical-src,controls,muted,class,style",
 "wrapper":"DETAILS.details-reset.border.rounded-2","fallbackKept":4}
```

The sanitiser **drops `poster`, `width` and the fallback content**. The authored `<video>` ends up identical to a bare URL, with the same six attributes. Reflex authored no `autoplay` or `loop`, so I did not observe those being stripped. I infer it from the fixed attribute set seen on both pages. Reflex's player therefore opens on its raw frame 0, a nearly empty code editor, instead of the poster the author supplied.

**For contrast, how an autoplaying GIF renders** (measured DOM, claude-code, crush, vhs, glow, zellij): every GIF is wrapped in `<animated-image>`, which carries a hidden player (`canvas.AnimatedImagePlayer-stillImage`, a "Play <alt>" button, and an open-in-new-window link).
- With default settings the original `<img>` is visible and animating.
- With Chrome launched with `--force-prefers-reduced-motion` (`matchMedia` returned true), the `<img>` is hidden. The canvas shows **frame 0 frozen** with a ▶ button in the top-right corner (`claude-code-reduced-motion.png`).

So for reduced-motion users, **frame 0 is the poster**.

## 5. Rules for our hero

1. **Ship an autoplaying GIF (or termtosvg SVG for pure terminal), not an MP4.** Both user-attachments videos I inspected (avante, reflex) rendered paused: autoplay=false, loop=false, poster stripped. It is boxed in a filename-labelled card and capped at 640 px high. Keep MP4 for an optional "full demo" further down.
2. **Make frame 0 a finished, legible statement and hold it 2.5–3.5 s.** Reduced-motion users and every video player see exactly frame 0. Only 1 of 20 heroes does this well (vercel/ai: a full sentence, held 3.2 s). claude-code shows its typed task and holds 2.6 s before one visible trigger. Do not open on an empty prompt, as 11 of 20 do.
3. **Also make every 3 s window stand alone, and keep the loop to 8–15 s.** The GIF clock starts at page load. The hero's top sits at a median of 2,062 px on the repo page (1,380–2,986 px at 1280×1400), so visitors land mid-loop. I measured landing at t≈10 s after a 9 s wait. Keep brand in frame the whole time and caption each action (lazygit, yazi). Match the last frame to the first so the seam vanishes (vercel/ai). The measured median loop is 12.6 s; beyond about 24 s (the upper quartile) the story is too slow to catch mid-loop.
4. **Design for an 838 px column with text at least 0.75× its capture size.** 838 px is the README width at a 1280 viewport. crush (1600 px capture shown at 800, 0.5×) and atuin (0.38×) are hard to read. claude-code is fine at 0.54× because its terminal font is large. cal.com (800 px) and magicui (640 px) are soft when upscaled. Capture at about 1600–1680 px only with a font enlarged to match. Otherwise author at 838–1000 px.
5. **Budget 1–5 MB and 10 fps for terminal, 25 fps for cursor/UI, with variable frame delays for holds.**
   - Autoplaying heroes: median 1.04 MB, upper quartile 4.89 MB, max 11.0 MB.
   - GitHub rejects GIF uploads over 10 MB, so commit the file if you must exceed it.
   - 10 fps carried claude-code, vercel/ai and zellij. VHS's 50 fps costs only 16 KB/s on flat terminal colour.
   - UI or gradient footage runs 388–521 KB/s at 20–33 fps, so 12 s ≈ 4.7–6.2 MB; crush's 1.38 MB/s is the cautionary case.
   - Use per-frame delays for still stretches: lazygit covers 14 s with 67 frames.
   - For both themes, put the capture on its own padded, framed backdrop (claude-code, vercel/ai). If you do ship variants, `<picture>` with `prefers-color-scheme` works for animated images (remotion, the only one of 20 that does).
