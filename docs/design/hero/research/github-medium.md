# GitHub as a medium for a README launch video: measured, 2026-09-24

Target repo: `renchris/agent-context-sync` (public, `repository_id` 1385005229, measured with `gh api repos/renchris/agent-context-sync --jq .id`; account plan `pro`, repo permission `admin`, measured with `gh api user --jq .plan.name` and `gh api repos/renchris/agent-context-sync --jq .permissions`).

Tags: **MEASURED** (command + output), **DOCUMENTED** (URL), **UNTESTED**.
Tools: `gh` 2.96.0; `agent-browser` 0.27.1 (headless Chromium 153, logged out, `prefers-color-scheme: dark`); Playwright `playwright-core` 1.63.0 driving the cached `~/Library/Caches/ms-playwright/chromium-1243` (Chromium 153.0.8010.12) and `webkit-2359` (WebKit 26.6). Nothing was logged into, uploaded, posted or changed.
Artifacts: raw `/markdown` inputs/outputs in `/tmp/hero-launch/gh-medium/md/*.in|*.out`; screenshots in `/tmp/hero-launch/gh-medium/` and `/tmp/hero-launch/gh-medium/probe/`; probe scripts `/tmp/hero-launch/gh-medium/probe*.cjs`; probe JSON in `/tmp/hero-launch/gh-medium/probe/result*.json`.

Real user-attachments video used as the test asset (found with `gh search code "user-attachments/assets" --filename README.md --limit 20`, then grepping the READMEs): `https://github.com/user-attachments/assets/8eaa697b-ffeb-4aea-8566-10a711a59b74`. It is a bare URL on its own line in `aviaryan/voice-writing-electron`'s README. MEASURED: `curl -sL -r 0-0 -D - -o /dev/null <url>` returned `302` to `github-production-user-asset-6210df.s3.amazonaws.com/4047597/356090012-8eaa697b-….mp4`, then `206`, `Content-Range: bytes 0-0/5211009`, `Content-Type: video/mp4`. So it is a 5.2 MB MP4, and clicking a plain link to it opens the raw MP4 in the browser's own player (there is no `content-disposition`).

---

## Part 1: what the renderer keeps

Command for every row: `gh api /markdown -f mode=gfm -f context=renchris/agent-context-sync -f text='<input>'`. `U` = the user-attachments URL above. Outputs are verbatim, with two exceptions: the octicon `<path d="…">` data is shortened to `<path …>`, and the JWT query string is shortened to `jwt=<JWT>`. The full raw outputs are in `md/<id>.out`.

### a. Relative-path `<video>`: stripped (MEASURED)
Input: `<video src="docs/media/x.mp4" controls muted autoplay loop></video>`
```html
<p dir="auto"></p>
```
The whole element is removed. Also stripped entirely (MEASURED, same command): `<video src="https://example.com/x.mp4" …>`, `<video src="https://raw.githubusercontent.com/renchris/agent-context-sync/main/docs/media/x.mp4" …>`, and `<video><source media="(prefers-color-scheme: dark)" src="U"></video>`. All three returned `<p dir="auto"></p>`. The only `<video>` that survives is one whose `src` is a user-attachments URL.

### b. `<video src="U" autoplay muted loop>`: rewritten to GitHub's own player; `autoplay` and `loop` dropped (MEASURED)
```html
<p dir="auto"><gh:secured-asset-reference resource_type="UserAsset" resource_id="356090012"></gh:secured-asset-reference></p><details open="" class="details-reset border rounded-2">
  <summary class="tmp-px-3 py-2">
    <svg aria-hidden="true" data-component="Octicon" height="16" viewBox="0 0 16 16" version="1.1" width="16" data-view-component="true" class="octicon octicon-device-camera-video">
    <path …></path>
</svg>
    <span class="m-1">voice-writing-demo.mp4</span>
    <span class="dropdown-caret"></span>
  </summary>

  <video src="https://github.com/user-attachments/assets/8eaa697b-ffeb-4aea-8566-10a711a59b74" data-canonical-src="https://github.com/user-attachments/assets/8eaa697b-ffeb-4aea-8566-10a711a59b74" controls="controls" muted="muted" class="d-block rounded-bottom-2 border-top width-fit" style="max-height:640px; min-height: 200px">

  </video>
</details>
<p></p>
```
Only `controls` and `muted` survive. I also rendered `<video src="U" poster="docs/media/p.png" autoplay muted loop playsinline controls width="100%">` (MEASURED): the output was identical, with `poster`, `playsinline`, `width`, `autoplay` and `loop` all dropped. So a README video cannot have a custom poster.
With `mode=markdown` instead of `gfm`, inputs a and b both returned `<p></p>` (MEASURED). The live README page does not behave that way (see "Live pages" below), so `/markdown` in `markdown` mode does not reproduce README rendering.

### c. Bare `U` on its own line: the same player, with a signed short-lived `src` (MEASURED)
```html
<details open="" class="details-reset border rounded-2">
  <summary class="tmp-px-3 py-2">
    <svg aria-hidden="true" data-component="Octicon" height="16" viewBox="0 0 16 16" version="1.1" width="16" data-view-component="true" class="octicon octicon-device-camera-video">
    <path …></path>
</svg>
    <span class="m-1">voice-writing-demo.mp4</span>
    <span class="dropdown-caret"></span>
  </summary>

  <video src="https://private-user-images.githubusercontent.com/4047597/356090012-8eaa697b-ffeb-4aea-8566-10a711a59b74.mp4?jwt=<JWT>" data-canonical-src="https://private-user-images.githubusercontent.com/4047597/356090012-8eaa697b-ffeb-4aea-8566-10a711a59b74.mp4?jwt=<JWT>" controls="controls" muted="muted" class="d-block rounded-bottom-2 border-top width-fit" style="max-height:640px; min-height: 200px">

  </video>
</details>
```
The server-side renderer does this itself; no page JS is needed. Decoding the JWT payload (MEASURED with python base64) gave `iss github.com`, `aud raw.githubusercontent.com`, `exp - nbf = 300` s, and the inner S3 URL carries `X-Amz-Expires=300`. The same `src` fetched about 16 minutes later returned `404` (MEASURED, `curl -s -o /dev/null -w '%{http_code}'`).
Related (MEASURED): `![](U)` renders as `<a …><img src="https://private-user-images…mp4?jwt=…">`, a broken image rather than a player. `U#gh-dark-mode-only` alone on a line renders the player with the fragment dropped, so the video shows in both themes.

### d. `<picture>` with a dark `<source>`: kept, wrapped in `<themed-picture>` (MEASURED)
```html
<p dir="auto"><themed-picture data-catalyst-inline="true"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/media/x-dark.webp"><img src="docs/media/x-light.webp" width="100%" alt="a"></picture></themed-picture></p>
```
(`/markdown` does not rewrite relative paths. On the live page they become `/<owner>/<repo>/raw/<branch>/<path>`; see the live-page rows below.)

### e. `<a href="U"><img …></a>`: a lone link to a video is REPLACED by the player and the image is lost (MEASURED)
Input (two paragraphs):
```html
<a href="U"><img src="docs/media/x.avif"></a>

<a href="U"><img src="docs/media/x.png"></a>
```
Output: two copies of the player from (c). The `<img>` does not appear anywhere in the output (full text in `md/e.out`). The same promotion happened (MEASURED) for:
- `[![a](docs/media/x.png)](U)`
- `<p align="center"><a href="U"><img …></a></p>`
- `<a href="U"><picture>…</picture></a>`
- `<a href="U#t=0"><img></a>`
- `<a href="U?download=1"><img></a>`

The link and image are KEPT (MEASURED) in these cases:
- the paragraph has other content: `<a href="U"><img src="docs/media/x.png"></a> Watch the launch video` returned `<p dir="auto"><a href="U"><img src="docs/media/x.png" style="max-width: 100%;"></a> Watch the launch video</p>`
- the link is inside a `<div>`:
  - `md/e12`: `<div align="center"><a href="U"><img src="docs/media/x.webp" width="100%"></a></div>` returned `<div align="center" dir="auto"><a href="https://github.com/user-attachments/assets/8eaa697b-ffeb-4aea-8566-10a711a59b74"><img src="docs/media/x.webp" width="100%" style="max-width: 100%;"></a></div>`
  - `md/e11`: `<div align="center"><a href="U"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/media/x-dark.webp"><img src="docs/media/x-light.webp" width="100%" alt="a"></picture></a><br><sub>Watch the 60-second launch film</sub></div>` returned
    ```html
    <div align="center" dir="auto"><a href="https://github.com/user-attachments/assets/8eaa697b-ffeb-4aea-8566-10a711a59b74"><themed-picture data-catalyst-inline="true"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/media/x-dark.webp"><img src="docs/media/x-light.webp" width="100%" alt="a"></picture></themed-picture></a><br><sub>Watch the 60-second launch film</sub></div>
    ```
- a link to a user-attachments IMAGE is not promoted: `<a href="https://github.com/user-attachments/assets/f1381a28-…"><img src="docs/media/x.png"></a>` returned `<p dir="auto"><a href="…f1381a28…"><img src="docs/media/x.png" style="max-width: 100%;"></a></p>`.

AVIF itself passes through (MEASURED): `<img src="docs/media/x.avif">` returned `<p dir="auto"><a target="_blank" rel="noopener noreferrer" href="docs/media/x.avif"><img src="docs/media/x.avif" style="max-width: 100%;"></a></p>`. Whether AVIF displays depends on the browser; that was UNTESTED on a live page.

### f. `![a](docs/media/x.webp#gh-dark-mode-only)`: the fragment is kept (MEASURED)
```html
<p dir="auto"><a target="_blank" rel="noopener noreferrer" href="docs/media/x.webp#gh-dark-mode-only"><img src="docs/media/x.webp#gh-dark-mode-only" alt="a" style="max-width: 100%;"></a></p>
```
`#gh-light-mode-only` behaves the same way. On a live page, the fragment is honoured by CSS on the parent link (MEASURED, `agent-browser open https://github.com/LukeMathWalker/pavex`, dark scheme, then an `eval` reading the computed style). The `#gh-dark-mode-only` image had parent `A` with `display:inline` and a 300×300 rect. The `#gh-light-mode-only` image had parent `A` with `display:none`, a 0×0 rect and `checkVisibility()` false. The docs no longer describe the fragment. The current "Basic writing and formatting syntax" page says only "The `<picture>` HTML element is supported." (DOCUMENTED, https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#the-picture-element).

### Live pages: README video players (MEASURED)
- **README, `<video autoplay loop src="U-webm">`**: `rosflight/rosplane` (README lines 12 and 15, `.webm` user-attachments). Commands: `agent-browser open https://github.com/rosflight/rosplane`; `agent-browser set viewport 1280 1400`; the brief's DOM `eval`. Screenshot: `gh-medium/01-rosplane-readme-video.png`. Both videos returned `autoplay:false, loop:false, muted:true, controls:true, poster:"", preload:"metadata", paused:true, w:836, h:470`. The `src` had been rewritten to `private-user-images.githubusercontent.com/…webm?jwt=…`, the attributes were only `src, data-canonical-src, controls, muted, class, style`, and the parent was `DETAILS` (open) with summary `rosplane_simulator_demo.webm`. Six seconds after scrolling into view, both were still paused at `currentTime` 0 (the second at 0.217).
- **README, bare URL line**: `aviaryan/voice-writing-electron`. Screenshot: `gh-medium/02-voice-writing-readme-bare-url.png`. Result: `autoplay:false, muted:true, loop:false, controls:true, poster:"", preload:"metadata", 836×627, paused, videoWidth 1600×1200, duration 44.96`.
- **Autoplay?** No, in either engine. The Playwright probe `probe/result.json` (T4/T5, both color schemes) returned `autoplay:false, paused:true` in Chromium 153 and WebKit 26.6. The only source attribute that survives is `autoplay` on a `<video src="U">`, and it is stripped server-side (row b).
- **What shows before play**: the first frame, not black. Chromium (`preload="metadata"`) paints frame 0 with the control bar and a muted icon: `probe/chromium-T4-*-video-before-play.png`, `chromium-T5-*`, `01-…png`. WebKit (property reads `preload="auto"`) paints frame 0 with a centred play button: `probe/webkit-T4-*`, `webkit-T5-*`. `canPlayType` returned `probably` for both VP9 WebM and H.264 MP4 in both engines. iOS Safari's first-frame behaviour was UNTESTED.
- **README or only issues?** Both. The two READMEs above are repo home pages. The "Attaching files" doc says upload happens in issues/PRs/comments (DOCUMENTED, link in Part 2), but the resulting URL renders in a README.
- **Signed-URL expiry on a long-open page** (MEASURED twice with `agent-browser eval` running `v.muted=true; await v.play()` on the `voice-writing-electron` README):
  - after the page had been open about 12 min: stopped at `currentTime` 1.02 with `MediaError.code` 2 (`MEDIA_ERR_NETWORK`), `paused:true`
  - repeat: loaded 19:58:16Z, played 20:03:55Z (5 min 38 s later): stopped at `currentTime` 1.01, `MediaError.code` 2, `paused:true`
  - control: a fresh load followed by an immediate `play()` reached `currentTime` 3.96 with no error

  A visitor who waits more than 5 min before pressing play gets a player that fails (a reload fixes it). This was measured in headless Chromium only.
- **Player size**: `style="max-height:640px; min-height: 200px"` plus `width-fit`. The README column is 836 to 838 CSS px (`article.markdown-body` `clientWidth` measured 838 at viewport widths 1280, 1440 and 1920 with `agent-browser set viewport`). A 960×540 WebM rendered 836×470; a 1600×1200 MP4 rendered 836×627.

---

## Part 2: limits

### User-attachments uploads (DOCUMENTED, https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files, fetched raw via `https://docs.github.com/api/article/body?pathname=…`, copy at `gh-medium/doc-attaching-files.md`)
- "10MB for images and gifs"
- "10MB for videos uploaded to a repository owned by a user or organization on a free GitHub plan"
- "100MB for videos uploaded to a repository owned by a user or organization on a paid GitHub plan". Note: "To upload videos greater than 10MB … you must either be an organization member or outside collaborator, or be on a paid plan." renchris is on `pro` (MEASURED), so the doc's limit is 100 MB.
- "25MB for all other files"
- Video formats: "Video (`.mp4`, `.mov`, `.webm`)". "Video codec compatibility is browser specific … At the moment we recommend using H.264 for greatest compatibility."
- Image formats supported "in all contexts": PNG, GIF, JPEG, SVG. WebP is not listed in the doc, but `gh`'s uploader accepts `.webp` as `image/webp` (DOCUMENTED, cli/cli `internal/attachments/userasset.go`, copy at `gh-medium/cli-attachments-userasset.go`).
- The `gh` client caps (same file): `maxImageBytes = 10 * 1024 * 1024` and `maxVideoBytes = 100 * 1024 * 1024`, with the comment "The real limit depends on the account plan … the server refuses the rest".
- Visibility: "For public repositories, uploaded files can be accessed without authentication."
- Server-side size refusal for >10 MB on free vs paid: UNTESTED (it would need an upload).

### Images in a README from a relative path
- Rewrite and serving (MEASURED, Playwright probe): `<img src="images/demo.webp">` in `micasa-dev/micasa` became `src="/micasa-dev/micasa/raw/main/images/demo.webp"`. `curl -sIL https://github.com/<o>/<r>/raw/HEAD/<path>` returned `302` to `raw.githubusercontent.com/<o>/<r>/<sha>/<path>`, then `200`, `content-type: image/webp` (or `image/gif`), `access-control-allow-origin: *`, `cache-control: max-age=300`. Relative-path images are NOT proxied through camo.
- No size cap below Git's own limit was found (MEASURED):
  - `raw.githubusercontent.com` served a 75,260,298-byte blob (`curl -sIL "https://github.com/owid/covid-19-data/raw/HEAD/public/data/cases_deaths/COVID-19%20Cases%20and%20deaths%20-%20WHO.csv"` returned `200`, `content-length: 75260298`).
  - A 5,447,632-byte relative GIF (`jesseduffield/lazydocker` `docs/resources/demo3.gif`, 1706×960) loaded and animated in both engines: `complete:true`, `naturalWidth:1706`, 4 of 4 samples distinct, `probe/result-bigimg.json`.
  - A README image between 10 MB and 100 MB was not found or tested live. UNTESTED.
- Git limits (DOCUMENTED, https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github):
  - "larger than 50 MiB, you will receive a warning from Git"
  - "GitHub blocks files larger than 100 MiB"
  - "via a browser, the file can be no larger than 25 MiB"
  - "ideally less than 1 GB, and less than 5 GB is strongly recommended"
- Single-object guidance (DOCUMENTED, https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits): "The recommended maximum limit is 1MB. This is enforced at 100 MB."
- README truncation (DOCUMENTED, https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes): "any content beyond 500 KiB will be truncated". This applies to the README text, not to linked media.
- Dimensions (MEASURED): the renderer adds `style="max-width: 100%;"` to every `<img>`, so images scale to the 836–838 px column. A 2400×1200 WebP displayed at 800×400 (it had `width=800`); a 1920×800 GIF at 838×349. No dimension cap was observed.

### Images from an absolute external URL go through camo, which has about a 5 MB cap (MEASURED)
`gh api /markdown -f mode=gfm -f text="![x](<url>)"` rewrites the image to `camo.githubusercontent.com/<hmac>/<hex-url>`. Fetching that camo URL with curl gave (`gh-medium/camo-sizes.txt`):
- origin 4,825,838 B returned `200 image/jpeg 4825838`
- origin 5,501,254 B returned `404 text/plain`, body `Content length exceeded`
- origin 6,891,807 B returned `404`
- origin 14,679,474 B returned `404`

So the cap lies between 4.83 and 5.50 MB; 5 MiB is consistent with that but is not pinned down. `raw.githubusercontent.com` and `github.com/…/raw/…` absolute URLs are NOT rewritten to camo (MEASURED).

### Reduced motion / "Autoplay animated images"
DOCUMENTED (https://docs.github.com/en/account-and-profile/how-tos/account-settings/managing-accessibility-settings): "You can control how GitHub displays animated *.gif* images. By default, GitHub syncs with your system-level preference for reduced motion." There is a per-user "Autoplay animated images" setting: Sync with system / Enabled / Disabled.
MEASURED (logged out, Playwright `reducedMotion: 'reduce'` vs `'no-preference'`, Chromium 153 and WebKit 26.6; `probe/result-reduced-motion.json`, `probe/result-gif-reduce.json`, screenshot `probe/webkit-reduce-lazydocker-gif.png`):
- **Plain relative `<img>` GIF** (lazydocker `demo3.gif`): the server wraps it in `<animated-image data-catalyst="">`.
  - `no-preference`: 4/4 frames distinct, so it animates
  - `reduce`: the original `<img>` is hidden and replaced by a still `CANVAS` with a `BUTTON[Play Gif]`; 1/4 frames distinct, so it is **paused** in both engines
- **Relative `<img>` animated WebP** (micasa `demo.webp`): no `<animated-image>` wrapper; **animates under `reduce`** (4/4) in both engines.
- **GIF inside `<picture>`** (geovista, camo URLs): no `<animated-image>` wrapper; **animates under `reduce`** (4/4) in both engines. A relative-path GIF inside `<picture>` was UNTESTED.
- A logged-in user's explicit "Disabled"/"Enabled" setting was UNTESTED (it needs a login).

### Does an animated WebP inside `<picture>`/`<source>` animate on github.com? (MEASURED)
- No public README with an animated WebP inside `<picture>` was found. I checked:
  - code search `prefers-color-scheme` (100 READMEs): the `<source>` WebPs were all static (`ANMF` count 0)
  - `getbeak/beak`, `mmailaender/Convex-Better-Auth-UI`, `ratneshchipre/ratneshc.com`, `margelo/react-native-vision-camera`, `Oxygem/Kanmail`: all static
- Test used instead (in-page swap on a live README): open `margelo/react-native-vision-camera` logged out. It has a real server-rendered `<themed-picture><picture><source srcset=…banner-dark.webp>`. Set every `<source srcset>` and the `<img src>` to `https://github.com/micasa-dev/micasa/raw/HEAD/images/demo.webp`, which is an animated WebP with 365 `ANMF` frames, 3,485,074 B. This is exactly what a relative path resolves to.
- The screenshot of the element was sampled 4 times at 1.3 s intervals (`probe/result.json` T3, `probe/result-t3-chromium-light.json`):

  | Engine | Scheme | `currentSrc` before the swap | Swapped-in WebP |
  |---|---|---|---|
  | Chromium 153 | dark | `banner-dark.webp` | loaded 2400 px wide, 4/4 frames distinct |
  | Chromium 153 | light | `banner-light.webp` | loaded 2400 px wide, 4/4 frames distinct |
  | WebKit 26.6 | dark | `banner-dark.webp` | loaded 2400 px wide, 4/4 frames distinct |
  | WebKit 26.6 | light | `banner-light.webp` | loaded 2400 px wide, 4/4 frames distinct |

  **It animates in both engines, and the theme switch picks the right source in both.**
- Unmodified live controls (T1, T2, both engines, both schemes, all 4/4 distinct):
  - micasa's animated WebP `<img>` (relative path)
  - `bjlittle/geovista`'s `<picture>` with light/dark animated GIF `<source>`s served via camo. `currentSrc` switched between the two camo URLs by scheme.
- Caveat: this is Playwright's WebKit 26.6 build, not Safari.app, and the page was modified client-side for the `<picture>` case. Safari.app itself was UNTESTED.

---

## Part 3: getting a user-attachments URL without posting anything (researched; nothing uploaded)

### Route A: the single-request token endpoint. It needs no cookie, and `gh` itself uses it (DOCUMENTED)
Source: cli/cli `internal/attachments/client.go` on trunk (copy at `gh-medium/cli-attachments-client.go`), from PR #14180 "`--attach` stack 4/8: Upload an asset to GitHub" (https://github.com/cli/cli/pull/14180). It shipped in `gh` v2.99.0 (release notes: "Attach images and videos to issues and pull requests … `--attach`", https://github.com/cli/cli/releases/tag/v2.99.0). Independently documented by `drogers0/gh-image` (`documentation/github-image-upload-flow.md`, https://github.com/drogers0/gh-image/blob/HEAD/documentation/github-image-upload-flow.md).
- Request: `POST https://uploads.github.com/user-attachments/assets?name=<file>&content_type=<mime>&repository_id=<numeric id>`, with the body the raw file bytes and `Content-Length` set.
- Headers:
  - `gh` sends `Content-Type: application/octet-stream` and `Accept: application/vnd.github+json`.
  - gh-image sends `Content-Type: <file mime>`, `Accept: application/json` and `Expect: 100-continue`.
  - Both put the credential in `Authorization`: gh-image as `Bearer <token>`; `gh` through its standard transport.
- Response: `201` `{"url":"https://github.com/user-attachments/assets/<uuid>"}`. The URL is returned directly; no issue or comment is created.
- Credentials: `gh` allowlists `TokenTypeOAuth`, `TokenTypePersonalAccess` and `TokenTypeFineGrainedPAT`. gh-image notes the Actions `GITHUB_TOKEN` is rejected. `gh` also requires `viewerPermission` ADMIN, MAINTAIN or WRITE ("READ and TRIAGE get a 404").
- Refusals (gh-image doc):
  - `422 content_type is not included in the list of allowed content types` for anything other than image or video
  - `404` for no push access or a missing `repository_id`
- Irreversible (DOCUMENTED in PR #14181, https://github.com/cli/cli/pull/14181): "An upload cannot be undone and there is no endpoint to delete an uploaded asset."
- Empty files store too: the `gh` comment says "Nothing downstream objects to zero bytes, so an empty file uploads and renders broken". **So the brief's "POST with size 0" probe WOULD store an asset. It was skipped.**
- What I sent, all anonymous (MEASURED):
  - `curl -X OPTIONS https://uploads.github.com/user-attachments/assets` returned `400 {"message":"Invalid name for request"}`
  - `curl` GET with no params returned the same
  - GET with `name/content_type/repository_id`, anonymous and via `gh api -X GET`, returned `400 {"message":"Bad Content-Length"}`
  - one anonymous POST with a 1-byte body (no credential, so nothing could be attributed or stored) returned `400 {"message":"You have sent an invalid request. Please do not send this request again."}`. I did not repeat it.
  - No authenticated POST was sent.
- **Can this machine's `gh` token do it?** The token is an OAuth token ( scopes include `repo`; MEASURED with `gh auth status`) and renchris has `admin` on the repo (MEASURED), so it meets `gh`'s allowlist. The live upload was UNTESTED on purpose.
- Local `gh` is 2.96.0, which has no `--attach`: `gh issue comment --help | grep -i attach` returned nothing (MEASURED); `brew info gh` shows 2.101.0 as stable. `--attach` needs ≥ 2.99.0 and only exists on `gh issue create/edit/comment` and `gh pr create/edit/comment`, all of which post (DOCUMENTED, https://docs.github.com/en/github-cli/github-cli/attaching-files-with-github-cli, copy at `gh-medium/doc-attaching-files-gh-cli.md`). Calling the endpoint directly with `curl` needs neither a newer `gh` nor a post.

### Route B: the web UI's 3-step cookie flow (DOCUMENTED by reverse-engineered tools; not exercised)
Sources:
- `drogers0/gh-image` (269★, pushed 2026-09-09): `documentation/github-image-upload-flow.md` and `internal/upload/upload.go`
- `sudosubin/gh-attach` (40★, pushed 2026-09-22): browser-cookie or `GH_ATTACH_SESSION_TOKEN` mode
- older: `zmwangx/ghuc` (2020), `maple3142/ghfileupl` (2022), `lisonge/user-attachments` ("upload file to github by cookie")

Located with `gh search code "upload/policies/assets" --limit 30`.

0. `GET https://github.com/<owner>/<repo>` with the `user_session` cookie, then scrape `"uploadToken":"…"` from the embedded JS payload. gh-image says a form's standard `authenticity_token` does NOT work here. MEASURED: the logged-out repo page also contains an `uploadToken` (`curl -s -A <Chrome UA> https://github.com/renchris/agent-context-sync | grep -c uploadToken` returned 1), but without a session it would be useless. The same page carries `<meta name="octolytics-dimension-repository_id" content="1385005229">`.
1. `POST https://github.com/upload/policies/assets`, multipart. Fields: `name`, `size` (must match exactly), `content_type`, `authenticity_token=<uploadToken>`, `repository_id`. Headers: `accept: application/json`, `origin: https://github.com`, `referer: https://github.com/<owner>/<repo>`, `x-requested-with: XMLHttpRequest`. Cookies: `user_session` plus `__Host-user_session_same_site` (same value; "Both must be present or GitHub returns 422"). The `201` response carries `upload_url` (S3), a `form` (key, acl `private`, policy, X-Amz-*, Content-Type, Cache-Control, …), `asset.id`, `asset.href`, `asset_upload_url` (`/upload/assets/<id>`) and `asset_upload_authenticity_token`.
2. `POST <upload_url>` (S3), multipart: every `form` field in order, then `file` last. There is no GitHub auth; do not add duplicate Content-Type/Cache-Control fields (that gives a 403 "Invalid according to Policy"). Returns `204`. The policy expires in about 30 min.
3. `PUT https://github.com<asset_upload_url>` with `authenticity_token=<asset_upload_authenticity_token>`, the same cookies and the same headers. Returns `200` with `href`. Without this step the href 404s.

What an agent would need for Route B: a logged-in github.com browser session cookie (`user_session`), the `uploadToken` scraped from the repo page, and `repository_id`. Nothing in this run obtained a session cookie, and none should be obtained without the user.

---

## Implications for our hero

- **What can autoplay inline**: only images. Tested:
  - an animated WebP or GIF by relative path, as `<img>` or inside `<picture>`/`<source>`: animated in Chromium 153 and WebKit 26.6 (MEASURED)
  - relative paths go through raw with no camo cap; a 5.4 MB GIF was measured working; Git's own limits are a 50 MiB warning and a 100 MiB block
  - absolute external image URLs are capped at about 5 MB by camo (MEASURED)
  - GitHub's reduced-motion handling (MEASURED): a plain `<img>` GIF is wrapped in `<animated-image>` and frozen behind a "Play Gif" button for reduced-motion visitors; an animated WebP (plain or in `<picture>`) is not wrapped and keeps playing. WebP is the smaller and more reliable autoplay format. The flip side is that it ignores the visitor's reduced-motion preference unless the loop is gentle.

  No `<video>` in a README ever autoplays. Relative, external and raw `<video>`s are deleted. A user-attachments video becomes a click-to-play `<details><video controls muted>` player with `autoplay`, `loop`, `poster` and `playsinline` stripped. It shows frame 0 before play, so frame 0 of the full film should be a designed title frame.
- **What can be light/dark**: images only:
  - `<picture><source media="(prefers-color-scheme: dark)" srcset="…-dark.webp"><img src="…-light.webp"></picture>` is kept and wrapped in `<themed-picture>`; the right source was chosen per scheme in both engines (MEASURED)
  - the `#gh-dark-mode-only` / `#gh-light-mode-only` fragments still work via CSS (MEASURED)

  The video player cannot be themed: `<source media>` in `<video>` is stripped and `#gh-*-mode-only` on a video URL is dropped (MEASURED).
- **Where a full video can live**:
  - as a user-attachments asset (MP4 H.264 recommended; ≤100 MB on renchris's `pro` plan per the doc, ≤10 MB on free), referenced as a bare URL on its own line to get the inline player
  - or as the `href` of the animated hero image, which opens the raw MP4 (`video/mp4`) in the browser. **The link must sit inside a `<div>` or share its paragraph with other content** (e.g. `<br><sub>caption</sub>`). A lone `<a href="U"><img></a>` in a paragraph is replaced by the player and the hero image is lost (MEASURED).

  Signed player URLs expire 300 s after page render. Pressing play more than 5 min after load failed with `MEDIA_ERR_NETWORK` at about 1 s (MEASURED twice, headless Chromium). That favours the link-to-MP4 route, which re-signs on each click, over relying only on the inline player. A repo file cannot serve as a README video at all.
- **Exact upload recipe (not executed)**. Irreversible: the asset cannot be deleted, so check the file before running.
  ```sh
  REPO_ID=$(gh api repos/renchris/agent-context-sync --jq .id)   # 1385005229
  F=hero-launch.mp4                                               # H.264 MP4, <=100 MB (pro)
  curl -sS -X POST \
    -H "Authorization: Bearer $(gh auth token)" \
    -H "Accept: application/json" \
    -H "Content-Type: video/mp4" \
    --data-binary @"$F" \
    "https://uploads.github.com/user-attachments/assets?name=$F&content_type=video%2Fmp4&repository_id=$REPO_ID"
  # expect 201 {"url":"https://github.com/user-attachments/assets/<uuid>"}
  ```
  Then put `https://github.com/user-attachments/assets/<uuid>` on its own line in the README for the player, and/or use it as the `href` of the div-wrapped animated `<picture>` hero.
  Alternatives:
  - `gh` ≥ 2.99.0 `--attach`, but it always posts an issue, PR or comment
  - the Route B cookie flow, which needs the user's browser session

  Both are UNTESTED here.
