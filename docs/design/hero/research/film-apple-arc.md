# Film study: Apple and Arc (The Browser Company). Motion grammar for a silent README hero

Scope: 4 official launch films, 2 per company. All four are short (18–56 s), so the brief's "two if short" applies.
- Apple primary: **Introducing Apple Creator Studio**. It is a software/services film, type-led, one word or line alone on black.
- Apple secondary: **Apple Card Design**. This is an object film with no kinetic type. I included it only as the reference for "one hero object alone on a field". I did not treat it as a typography reference.
- Arc: **Arc 1.0 | Everything or nothing** (the 1.0 launch film) and **Arc Max** (a feature launch). I skipped "Meet Arc" and "Arc Search" because the only official "Meet…" film found is 14 min ("Meet Act II…"), and the Arc Search item is a TV commercial.

## Sources

| Film | URL | Channel | Published | Length | Download (ffprobe) |
|---|---|---|---|---|---|
| Introducing Apple Creator Studio | https://www.youtube.com/watch?v=ISYIEmQxs2M | Apple | 2026-01-13 | 35.0 s | 1920×1080, 23.976 fps |
| Apple Card Design | https://www.youtube.com/watch?v=JLE228ZuGGA | Apple | 2019-03-27 | 56.0 s | 1920×1080, 59.94 fps |
| Arc 1.0 \| Everything or nothing | https://www.youtube.com/watch?v=n5Vwrj1gEWs | The Browser Company | 2023-07-25 | 34.0 s | 1672×1080 (1.55:1), 29.97 fps |
| Arc Max \| Bringing AI to Arc Browser | https://www.youtube.com/watch?v=ttylMKwIe7c | The Browser Company | 2023-10-05 | 18.0 s | 1920×1080 with pillarbox (active area 1616×1080, 1.50:1), 29.38 fps |

Total downloaded: 13 MB, video-only (measured with `du -ch`). Caption tracks were fetched only to learn whether a film has voice-over or sound effects (`yt-dlp --write-auto-subs`).

### How every number below was obtained (tag after each number)
- **m:scene**: measured. Cut detection with `ffmpeg -vf "select='gt(scene,0.3)',metadata=print"`, plus a scene score for every frame (`select='gte(scene,0)',signalstats`) saved to `/tmp/hero-launch/films/work/<film>.csv`. A hard cut is a one-frame spike.
- **m:track**: measured. `work/track.py` records, for every frame at native rate (OpenCV, fixed threshold), the bounding box, centroid and 99th-percentile brightness of the lit pixels.
- **m:meas**: measured. `work/measure.py` finds connected components on a full-resolution frame. Cap height is the box of the first capital letter, expressed as % of frame height (H). Widths are % of frame width (W).
- **m:scale**: measured. `work/scale.py` matches ORB features between two frames and fits a partial affine with RANSAC, which gives the camera or layer scale and shift.
- **m:curve**: measured. `~/Development/claude-infrastructure/tools/motion-film/motion-probe.py curve`, which fits easing curves to a centroid tracked with Otsu thresholding.
- **m:fit**: measured. `work/fit.py` fits the probe's own easing table to an edge series from m:track. I used it where Otsu's threshold flipped between foreground and background.
- **m:luma**: measured. Mean brightness (signalstats YAVG). 16 means black because the video uses limited range.
- **m:caps**: measured. Text of the YouTube caption track.
- **m:slice**: measured. `motion-probe.py slice --marks` over 0–10 s.
- **e:sheet**: estimated by reading contact sheets at 4–24 fps. Accurate to about ±1 sample.

---

## Film A: Introducing Apple Creator Studio (Apple, 35.0 s): the type reference

| Item | Measurement |
|---|---|
| Sound | Music plus sound effects, no voice-over. Captions contain only: ♪ "NOBODY" by ZEP ♪, [CLICK], [CRINKLING], [WHOOSH] (m:caps). |
| t = 0 | Pure black (Y 16) until 0.083 s, when the first glyphs appear (m:track). |
| t = 1 s | Clean white wordmark "[Apple logo] Creator Studio" centred on black. Cap height 11.3 % H, width 60.3 % W (m:meas). |
| t = 3 s | Full-bleed collage of about 20 tiles of app UIs and creative work: Final Cut, Logic, Pixelmator, Keynote (e:sheet count). |
| Product known by 3 s? | **Yes.**<br>• All glyphs of the name are on screen by 0.42 s, and it reads clean from 0.92 s.<br>• The collage at 1.54–3.29 s says "Apple's creative apps".<br>• The value line "One subscription." lands at 3.92 s (m:track). |
| Shot length | ffmpeg with scene > 0.3 finds **1 cut in 35 s** (m:scene). The film is one continuous motion-graphics take, so I counted compositional beats instead:<br>• n = 24<br>• median 0.92 s<br>• min 0.42 s ("Endless")<br>• max 4.04 s ("Take your design anywhere.")<br>Boundaries come from m:track and m:scene, accurate to ±0.08 s. |
| Transitions (26 boundaries) | • In-place hard swap of the text: **11**<br>• Hard cut to black: **1**<br>• Morph, where one element turns into the next (text→Dock, letters→drum hits, words→paper ball→Trash, "Share it."→brush-stroke shatter): **8**<br>• Zoom-through match cut: **1**. The Dock's Logic icon grows to fill the frame and its circle opens on "Make a beat."<br>• Split-reveal wipe: **1**. The collage parts to reveal "One".<br>• Whip: **1**. The iPad leaves with motion blur, 15.89–16.18 s = 0.29 s.<br>• Vertical roll or push: **2**<br>• Crossfades: **0**<br>(e:sheet at 6–24 fps) |
| Type: family and weight | SF Pro Display, Apple's system grotesque, medium to semibold. Stem is about 0.15–0.16 × cap height ('i' stem 16–23 px at cap 107–146 px) (m:meas). |
| Type: case and tracking | Sentence case, and every statement ends with a full stop. Default display tracking: the gap between 'n' and 'e' is 0.11 × x-height (m:meas). |
| **Cap height: hero** | Verb cards ("Prompt it.", "Loop it.", "Scrap it.", "Move it.", "Upscale it."): **13.1–13.9 % H, median 13.5 % H**. Line width 31–46 % W, centred at 48–51 % H (m:meas). At 1280×720 that is a **97 px cap**. |
| Cap height: statements | 9.2–11.5 % H (m:meas):<br>• "One" 10.7 %<br>• "Endless" 10.2 %<br>• "One subscription." 9.9 %<br>• "Endless creativity." 9.2 %<br>• "Make a beat." 11.5 %<br>• price line 9.7 %<br>• wordmark 11.3 %<br>• final lockup 10.9 % at 58.2 % W<br>The headline inside the device ("Take") is about 23 % H, box including the ascender. |
| Cap height: secondary | The only secondary text is the legal line "Subscription required.": box 1.8 % H, cap about 1.3 %. Hero is roughly 7–10× bigger (m:meas). |
| Words per card | 1–4, median 2 (e:sheet). |
| Entrance: wordmark | 14 glyphs pop in between 0.083 and 0.417 s, about 24 ms each (one glyph per frame), in 3 clusters moving left to right. Each glyph starts in a different app's style and snaps to SF by 0.92 s (m:track, count of connected components per frame). |
| Entrance: "One" | Slides +15 % W in 0.42 s with a strong ease-out. Probe curve on a transposed clip: **outQuart RMSE 0.030 against linear 0.308, "decisive"** (m:curve). |
| Entrance: appended words | "subscription." and "creativity." appear hard, in 1 frame. The whole line then **pops +4.5 % in width over 2 frames (84 ms)** (m:track). |
| Entrance: "Prompt it." | Brightness goes from 185 to 255 in 1 frame (42 ms), so it is effectively a hard entrance. The motion is carried by the iPad leaving the frame (m:track). |
| Entrance: price line | "Get it" drops in from 1.43× to 1.0× scale in 0.25 s. Then "for $12.99/mo." wipes on left to right in 0.17 s (4 frames) while the line re-centres by 3 % W (m:track). |
| Entrance: "Edit a video." | Appears hard in the wrong order ("video. Edit a") and the words rearrange into place in about 0.25 s (e:sheet at 24 fps). |
| Entrance: blur | No blur-in on text, except a soft glow on "Share it." (e:sheet). |
| Entrance: lockup | Rises 10.9 % H while fading in (brightness 60→255 in 0.21 s). Per-frame steps are 34, 26, 21, 17, 14, 6 px, so it is an ease-out. fit: outQuad 0.038 against linear 0.132. The probe's curve was inconclusive because the glitch reprise cuts the rise short (m:track + m:fit). |
| **Holds** | Nothing holds still. Each line shrinks while it is on screen:<br>• "One subscription.": 56.2→51.8 % W in 0.38 s (−7.8 %, accelerating)<br>• "Endless creativity.": 56.2→50.5 % W in 0.67 s (−10 %)<br>That is about **−15 to −21 % per second** (m:track). |
| **Card durations** | • Statements: 0.42–0.63 s<br>• Verb cards: 9 verbs in 9.64 s, **0.62–2.09 s each, median 0.92 s**<br>(m:scene/track) |
| Colour | • Background #000 for 100 % of the runtime (m:luma).<br>• White type.<br>• At most 2 accent hues per card, taken from the app icons (violet, lime, amber, blue, magenta…). About 8 distinct hues across the film.<br>• "$12.99" is the only word in several colours (e:sheet).<br>• Dark only. There is no light scene. |
| Space and camera | Flat 2D with no camera. Depth comes only from scale:<br>• The collage explodes from a central cluster about 15 % W wide (1.54 s) to full bleed by about 1.66 s, roughly ×6–7 in 0.15 s (e:sheet at 24 fps).<br>• The tiles then drift independently for 1.4 s; the slice shows curved bands, which means the drift is eased (m:slice).<br>• The Dock (10 icons) plays the macOS magnification wave from 5.75 to 6.9 s. |
| Many → one | Used 3 times:<br>• collage → "One"<br>• Dock → one icon filling the frame<br>• app-name roll (10 names, 0.155 s each) → one lockup<br>(m:track/sheet) |
| Memorable image | **"Scrap it."** (cap 13.9 % H) with a translucent macOS Trash fading up beneath it. The line crumples into a paper ball at 22.02 s and drops in, and the Trash holds for 1.3 s. It sticks because the word performs its own verb using an object everyone already knows. |
| Ending | 1. App-name roll, 27.35–28.62 s<br>2. Lockup rises, 28.62–28.95 s<br>3. 0.4 s glitch reprise of the opening glyph mix (a callback)<br>4. Clean lockup holds **0.68 s** (29.35–30.03)<br>5. Hard cut to black, which stays **5.0 s** (to 35.03)<br>(m:track/luma)<br>Loopable: it starts and ends on pure black. |
| With no sound | Survives: the verb cadence of about one card per 0.9 s, the literal verb animations, and the constant drift.<br>Lost: the sound-effect punchlines ([CLICK] on Select, [CRINKLING] on Scrap, [WHOOSH] on the whip) and the "Nobody / no no" vocal hook that the 0.42–0.63 s cards are cut to (m:caps). |

## Film B: Apple Card Design (Apple, 56.0 s): the single-hero-object reference

| Item | Measurement |
|---|---|
| Sound | No voice-over. Captions contain "♪ Orchestral music ♪" and "[MECHANICAL WHIRRING]" (m:caps). |
| t = 0 | Black (Y 16). |
| t = 1 s | Nearly black, with faint vertical highlights on machined titanium (Y 18.4) (m:luma). |
| Opening light | Light ramps up from 0.5 to 4.3 s (Y 16→102). This is a lighting reveal, not a fade (m:luma). |
| t = 3 s | Macro shot of lathe-turned titanium, grey (Y 65.6). |
| Product known by 3 s? | **No.**<br>• The Apple logo is first etched at about 24.8 s.<br>• The card is shown as a product only at about 35 s. |
| Shot length | 14 shots: **median 2.30 s, min 1.44, max 13.80** (the final shot) (m:scene). |
| Cuts | • All 13 transitions are one-frame hard cuts.<br>• 0 crossfades.<br>• A hard cut from white (Y 204) to black at 48.02 s.<br>(m:scene/luma) |
| Type | No titles.<br>• The name "Marisa Robertson" is laser-etched letter by letter at about 4.7 letters per second, roughly 210 ms per letter (e:sheet, from letter counts at 1 fps).<br>• Legal line: 1.5 % H tall at 94 % H, on screen 42.89–48.00 s (m:track). |
| Colour and light/dark | • Neutral greys throughout, with one small magenta accent (the probe's ruby tip, about 12–13 s) and white laser sparks.<br>• The film travels from dark to light: 8.8 s of dark macro shots, then about 39 s on a bright white seamless background.<br>• End frame Y 204, a gentle corner gradient from #BBBBBD to #EDEDED (m:luma/meas). |
| Space and camera | Real 3D macro photography with shallow depth. The camera is almost still:<br>• Nozzle shot: −1 % scale over 3 s.<br>• Final shot: scale **1.000 ± 0.003 over 10.5 s** (m:scale). |
| Hero object | **One card, 20.6 % W × 23.5 % H**, centred on an empty white field, with about 79 % of the width left empty (m:meas).<br>Scale is shown by contrast: machines and extreme macro shots, then this one small object. |
| Memorable image | A hand places the lone white card in the centre of the white field (about 35–37 s). The card then sits alone for about 11 s with a soft contact shadow; the hand returns once to nudge it 2.3° (m:scale). It sticks through emptiness after 30 s of dense machinery. |
| Ending | No logo card and no lockup. It ends on the object plus the legal line: one locked shot of **13.8 s** (34.22–48.02), a hard cut to black, then **8.0 s of black** (m:scene/luma).<br>The white-to-black snap would be harsh as a loop point. |
| With no sound | Survives: the light-up, the sparks, the final object.<br>Lost: the pacing. The 2.3 s median shots and the 13.8 s static ending are carried by the orchestra and the machine sounds; silent, they read as dead air. |

## Film C: Arc 1.0 | Everything or nothing (The Browser Company, 34.0 s)

| Item | Measurement |
|---|---|
| Sound | There is no caption track, so I could not check for voice-over. The whole sentence is on screen as text. |
| t = 0 | Archival black-and-white footage of early aviation: a launch tower on a river with a plane on its catapult. Film grain plus gate weave: the horizon wobbles by about ±1 % H (m:slice). |
| t = 1 s | The plane is leaving the tower (1.5–2.0 s). |
| t = 3 s | The plane has plunged into the water and the tower stands empty. |
| Product known by 3 s? | **No.**<br>• The word "version" appears at 3.94 s and "1.0s" at 4.50 s.<br>• The name "Arc" appears only at **24.39 s**, and the first UI at 25.93 s.<br>(m:track/scene) |
| Shot length | 29 shots: **median 0.80 s, min 0.53 ("no"), max 5.14 (the opening archival shot)** (m:scene + m:track).<br>21 of the 29 fall between 0.67 and 0.97 s. |
| Cuts | • 28 hard cuts and 0 dissolves.<br>• 11 of the cuts are graphic-match cuts: they keep an identical white "v1.0" frame, so the montage reads as one card being flipped (e:sheet).<br>• In addition, 2 words cut in hard over the archival shot. |
| Type: family and case | Soft serif with ball terminals, regular weight. Lowercase except "Arc", "Available" and "Today".<br>Tracking: the gap between 'n' and 'e' is about 0.12 × x-height (m:meas). |
| Type: sentence words (on black) | x-height 7.5 % H, ascender 11.3 %, cap about 10.7 % H. Lines are 10–68 % W wide, centred at 50/50 (m:meas). |
| Type: overlay on footage | • "version": x-height **19.6 % H**, spanning **93 % W**<br>• "1.0s": figure 28.8 % H<br>(m:meas) |
| Type: product name | "Arc" and "v1.0": cap **25–26 % H**, about 2.4× the size of the sentence words (m:meas). |
| Type: card captions | Lowercase serif (for example "amphibious cycle"), x-height 2.0 % H, with "v1.0" in both bottom corners.<br>Card frame: 3 px white rounded rectangle, 80 % W × 79.5 % H (m:meas). |
| Type: end cards | Geometric sans, medium, lowercase: "download now" 6.2 % H, "arc.net" 5.8 % H (m:meas). |
| Words per card | 1–3, **median 1**. The sentence "version 1.0s / can mean / everything / … / no → nothing" is spread over 6 cards between 17 montage shots.<br>"nothing" arrives in two steps: "no" (0.53 s), then "nothing" (0.67 s) (m:track). |
| Entrances and holds | Every word cuts in and out hard. Its box stays identical to the pixel for the whole hold: no drift, no fade (m:track).<br>Text-card holds: **0.53–1.33 s, median 0.72 s** (m:track). |
| Colour and light/dark | • From 0 to 25.9 s (76 % of the runtime) there is no brand colour: black-and-white archive, white-on-black type, and found clips (some in colour) always inside the same black and white frame.<br>• Brightness flips between grey footage (Y≈160) and black cards (Y≈19) about every 0.7 s (m:luma).<br>• Brand colour arrives with the product: a pastel theme picker changes hue about every 0.33 s (e:sheet), then a solid blue **RGB (4, 65, 245)** fills the last **2.97 s** (m:meas). |
| Space and camera | Flat cards with no camera moves on the text; the slice lines are flat (m:slice).<br>One large move: the UI pulls back from **1.00× to 0.50× over 1.65 s** (26.05–27.70 s). It accelerates the whole way and stops dead. Best fit **inQuad RMSE 0.015**, against linear 0.161 (m:scale + m:fit).<br>"Many things" is a museum of 17 v1.0 inventions, each in the same frame. "One thing" is "Arc". |
| Memorable image | The giant word "version" laid over the early aircraft that has just fallen into the river. It turns a failure into the brand's thesis. |
| Ending | "Available to all" 0.70 s → "Today" 0.73 → logo on blue 0.87 → "download now" 0.77 → "arc.net" 1.33 → end. No black tail (m:track).<br>Not loopable as it stands: the head is grey archive and the tail is blue. |
| With no sound | Survives well. The whole argument is written on screen, and the rhythm comes from the nearly regular 0.67–0.97 s cadence of cards and shots (m:scene). |

## Film D: Arc Max | Bringing AI to Arc Browser (The Browser Company, 18.0 s)

| Item | Measurement |
|---|---|
| Sound | Music only; the caption track reads "[Music]" (m:caps). |
| Format | Pillarboxed: 152 px black bars on each side, active picture 1616×1080 = 1.50:1 (m:meas). |
| t = 0 | TV static (grey noise), 0.00–0.55 s. |
| t = 1 s | Hard cut at 0.55 s to a tight crop of a Google results page inside an Arc window over a leaf wallpaper. The cursor hovers a link (m:scene). |
| t = 3 s | An AI hover-preview card is open under the link. |
| Product known by 3 s? | **Partly.**<br>• "A browser with an AI preview" is readable from about 1.3 s.<br>• The name (the "Max" logo) appears only at **15.89 s** (m:scene/track). |
| Shot length | 15 shots: **median 1.12 s, min 0.47, max 2.75** (m:scene). |
| Cuts | • 14 hard cuts, 0 dissolves.<br>• 2 of the cuts are punch-ins on the same UI: **×1.87** at 4.15 s and **×1.77** at 10.69 s (m:scale). |
| Type | Only on the 2 end cards (UI text is screen recording):<br>• geometric sans, medium, sentence case, no final full stop<br>• cap **5.7 % H**, x-height 4.4 % H, which is 41 px and 32 px at 720p<br>• 'n' to 'e' gap 0.13 × x-height (m:meas)<br>• 4–5 words per card<br>• hard entrance, static<br>• holds 0.95 s and 1.12 s (m:track) |
| UI element motion: toast | The download toast grows up from its anchor in 0.24 s, **overshoots by 17 px (about 6 % of its travel)**, and settles over 0.27 s. That is a spring-like ease-out:<br>• fit: outExpo 0.073, outBack 0.105, linear 0.458<br>• the probe's curve was inconclusive because Otsu's threshold flipped<br>(m:track + m:fit) |
| UI element motion: panel | The "Ask" panel snaps open in 2 frames (68 ms) (m:track). |
| Colour | Natural photographic wallpapers plus UI. End cards are blue-violet **RGB (51, 45, 242)** with white type. The Max logo is white with a pink sparkle (m:meas). |
| Space and camera | Flat screen recording under a **constant linear push-in of +3.9 % per second**: 1.000→1.098 over 2.5 s, in equal 0.5 s steps (m:scale). No parallax. |
| Memorable image | The TV-static bookends, a "channel flip" into and out of the film. |
| Ending | "Because the small moments" 0.95 s → "add up to big ones" 1.12 s → Max logo 1.50 s (24.4 % W × 19.3 % H) → static 0.58 s (m:track).<br>**Seamless loop by design:** 0.55 s of static at the start matches 0.58 s at the end. |
| With no sound | The UI demos read without sound, because each cursor action has a visible effect. The end copy is on screen. Nothing essential needs audio. |

---

## Grammar rules to steal

1. **Rule 1: one line, about 13 % cap height, centred, alone on the field.**
   - Apple's verb cards measure a median cap height of **13.5 % H** (13.1–13.9).
   - Lines of 2–3 words drop to **9–11 % H**.
   - Keep the line **≤ 60 % W**, centred within ±1 %.
   - At 1280×720 that is a **97 px cap** for the hero word and 66–79 px for a short sentence.
   - Secondary text should be at least 7× smaller, or left out.
2. **Rule 2: change the card every ~0.9 s, and build sentences by adding words.**
   - Median card length is **0.92 s** for Apple's verbs and **0.72 s** for Arc's words.
   - A one-word card can be as short as **0.42–0.63 s** if the next card extends it, as in "One" → "One subscription." Add the new word in 1 frame; do not replace the line.
   - The regular card change is the silent metronome.
3. **Rule 3: never fully still. Shrink during the hold, pop on arrival.**
   - During a hold, scale the line down about **15–20 % per second** (−8 to −10 % over a 0.4–0.7 s hold).
   - Then cut to the next line, which settles **+4.5 % in 2 frames** (84 ms).
   - Arc's cards are completely static; next to Apple's, they read as slides.
4. **Rule 4: move in with a front-loaded ease-out, travel about 15 % of the frame, take about 0.4 s.**
   - "One" slides **15 % W in 0.42 s**. Its best fit is outQuart, and it clearly beats linear (RMSE 0.030 against 0.308).
   - The lockup rises **10.9 % H with a fade in about 0.25 s**, also eased out.
   - A hard entrance (≤ 2 frames, 42–84 ms) works when the outgoing element carries the motion, such as a 0.29 s whip.
   - The only ease-in in these films is Arc's pull-back (1.0→0.5 scale in 1.65 s, inQuad), and it ends in a hard stop.
   - The probe separates eased from linear motion, but it cannot tell outExpo from outBack; the design choice here is simply "eased and front-loaded".
5. **Rule 5: the word performs its own verb.**
   - Apple shows **9 verbs in 9.64 s**, each **0.62–2.09 s long (median 0.92)**, each animated as its own action:
     - Scrap → crumples into the Trash
     - Loop → mirrors
     - Upscale → pixel sweep
     - Select → highlight sweep
     - Write → caret types, then the text runs along a path
   - For a developer tool, a verb such as run, diff or ship gets a 0.6–1.0 s micro-behaviour of the tool itself.
6. **Rule 6: show scale as many things collapsing into one, not with the camera.**
   - Apple uses this three times:
     - A collage of about 20 tiles explodes from a 15 % W cluster to full bleed in about 0.15 s, drifts for 1.4 s, then parts to leave one word.
     - A 10-icon Dock gives way to one icon that fills the frame and opens as an iris.
     - 10 app names roll at **0.155 s each** into one lockup.
7. **Rule 7: one object, about 20 % wide, about 80 % empty, camera locked.**
   - The Apple Card measures **20.6 % W × 23.5 % H**, centred.
   - Camera scale drift is **≤ 0.3 % over 10.5 s**. The emptiness is what makes it the hero.
   - A long hold (Apple uses 13.8 s) only works if something small changes: a hand, a shadow, a 2.3° nudge.
8. **Rule 8: hold the brand colour back until the reveal.**
   - Arc spends **76 %** of its runtime in black-and-white chrome and gives its blue (RGB 4, 65, 245) only the last **2.97 s**.
   - Apple keeps a #000 field with **at most 2 accent hues per card**.
9. **Rule 9: a template frame turns a fast montage into one object.**
   - Arc uses an identical **3 px white rounded frame at 80 % W × 79.5 % H**, with a lowercase caption at **2.0 % H** x-height and "v1.0" in the corners.
   - With that frame, **11 hard cuts at 0.67–0.97 s** read as one card being flipped rather than as 11 edits.
10. **Rule 10: start and end on the same frame, and say the name within the first second.**
    - Arc Max starts with **0.55 s** of static and ends with **0.58 s**: a free seam.
    - Apple starts with **0.083 s** of black; its 5.0 s black tail should be trimmed.
    - Apple has the name on screen by **0.42 s** (clean at 0.92 s) and states the value by **3.92 s**.
    - Apple's clean lockup hold is only **0.68 s**. That works in a loop because the next pass shows it again.

## Do not steal

**Needs sound**
- Creator Studio's sound-effect punchlines ([CLICK], [CRINKLING], [WHOOSH]) and the "NOBODY / no no" vocal hook that the 0.42–0.63 s "One" and "Endless" cards are cut to. Cards that short are readable without sound only when they add to the previous card (rule 2).
- Apple Card's 3.8 s light-up from black and its 13.8 s locked final shot. Both are carried by the orchestra and machine sound; silent, they are dead air.
- Arc 1.0's comic beat on "everything … nothing". I could not verify its audio because the film has no caption track.

**Needs real product footage or third-party material**
- Apple Card's macro cinematography (lathe, spray nozzles, laser etching, sparks) and the hand placing the card.
- Creator Studio's device shots: MacBook, iPad with Pencil, hands on Live Loops pads, and the real app UIs in the collage.
- Arc Max's screen recordings of Google, Gmail, Eater and ChatGPT.
- Arc 1.0's archival film and clips of famous products (Twitter bird, Pixar, Google Glass, Segway). These carry licensing issues and borrowed meaning.

**Wrong for a README**
- Long black tails. Creator Studio ends with 5.0 of 35.0 s in black and Apple Card with 8.0 of 56.0 s, so about **14 % of every loop** would be black (m:luma). Trim to ≤ 0.5 s.
- Arc Max's 1.50:1 pillarbox (16 % of the width is black bars) and its end-card cap of 5.7 % H (41 px at 720p, and smaller again once GitHub scales the video down).
- Legal fine print at 1.5–1.8 % H: unreadable at README size.
- Full-screen TV static, archival grain, and single flash frames (Arc 1.0 has one flash near 1.13–1.20 s, with scene spikes of 0.13–0.14). In an autoplay loop these flicker, which is a photosensitivity concern. They are also expected to inflate file size, though I did not measure that.
- Full-bleed fields that are pure #000 (Creator Studio) or near white (Apple Card ending, Y 204). Each becomes a hard slab on one of GitHub's two themes.
- A hard white-to-black snap at the loop point (Apple Card at 48.02 s: Y 204→16 in one frame).
- Naming the product late: Arc names it at 15.9 s (Max) and 24.4 s (1.0).
- Arc 1.0's "version" overlay at 93 % W over busy footage. It only reads because the film is shown full-screen.

## Files made under /tmp/hero-launch/films/

**Films**, video only, 13 MB total:
- `/tmp/hero-launch/films/apple-creator-studio.mp4`
- `/tmp/hero-launch/films/apple-card-design.mp4`
- `/tmp/hero-launch/films/arc-1-0.mp4`
- `/tmp/hero-launch/films/arc-max.mp4`

**Contact sheets.** Exact 1 frame per second, 4 columns, labelled with the true second:
- `/tmp/hero-launch/films/apple-creator-studio-sheet00.png` … `sheet02.png`
- `/tmp/hero-launch/films/apple-card-design-sheet00.png` … `sheet03.png`
- `/tmp/hero-launch/films/arc-1-0-sheet00.png` … `sheet02.png`
- `/tmp/hero-launch/films/arc-max-sheet00.png`, `sheet01.png`
- The frames behind them are in `/tmp/hero-launch/films/<film>-1fps/`.

**Key frames.** Full resolution at t = 0, 0.25, 0.5, 1, 1.5, 2, 3, 4, 5 s:
- `/tmp/hero-launch/films/apple-creator-studio-key/`
- `/tmp/hero-launch/films/apple-card-design-key/`
- `/tmp/hero-launch/films/arc-1-0-key/`
- `/tmp/hero-launch/films/arc-max-key/`
- A 3×3 overview of each: `/tmp/hero-launch/films/<film>-keysheet.png`

**Type and measurement frames**, full resolution, named `t<seconds>.png`:
- `/tmp/hero-launch/films/apple-creator-studio-type/`: wordmark, "One", "Endless creativity.", "Prompt it.", "Scrap it.", Trash, "Move it.", "Upscale it.", price line, app roll, lockup.
- `/tmp/hero-launch/films/apple-card-design-type/`: plate, etched logo, name, hand placement, card alone at t44.0.
- `/tmp/hero-launch/films/arc-1-0-type/`: "version", "1.0s", framed card, word cards, "Arc", "v1.0", blue end cards.
- `/tmp/hero-launch/films/arc-max-type/`: UI crop, the 2 blue cards, the Max logo.

**Slit-scans** (`motion-probe.py slice --marks`, 0–10 s):
- `/tmp/hero-launch/films/work/cs-slice-0-10.png`
- `/tmp/hero-launch/films/work/card-slice-0-10.png`
- `/tmp/hero-launch/films/work/arc10-slice-0-10.png`
- `/tmp/hero-launch/films/work/arcmax-slice-0-10.png`
- Each has a horizontally stretched `*-wide.png` copy for easier reading.

**Dense working sheets** (4–24 fps): `/tmp/hero-launch/films/work/<film>-dense-<from>-<to>/sheet*.png`.
- They were built with the ffmpeg `fps` filter, so each label is about half a sample earlier than the frame it shows. Use the per-frame data below for exact times.

**Per-frame data:**
- `/tmp/hero-launch/films/work/<film>.csv`: time, scene score, YAVG, YMIN, YMAX
- `/tmp/hero-launch/films/work/arc10-zoom-series.txt`: Arc 1.0 pull-back scale series
- `/tmp/hero-launch/films/work/cs-one-slide-T.mp4`: the transposed clip used for the "One" curve fit
- `/tmp/hero-launch/films/work/subs/*.vtt`: caption tracks

**Scripts:** `/tmp/hero-launch/films/work/track.py`, `measure.py`, `scale.py`, `fit.py`, `frames.sh`, `dense.sh`, `sheet1fps.sh`
