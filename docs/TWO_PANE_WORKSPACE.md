# Two-pane workspace and UI overhaul: spec

Status: approved direction (Kevin, 2026-11-27), built as v1.26.0, a local test build until he says ship.
Origin: the Suikoden III editor got this treatment first (its spec lives in that repo as
docs/TWO_PANE_WORKSPACE.md): 23 views in one clipping chip strip, no grouping, no per-view
dirty markers, session actions scattered, and a 900px column wasting desktop width. Kevin
picked the same structural fix for this editor, with small screens a first-class target
(AYANEO Pocket Micro 2, 3.5in 960x640 panel), the full quick-win set, and an embedded art
set in this game's own Falena dawn idiom. The Suikoden III fire art and palettes are NOT
carried over; the structure and behaviour are.

## Layout

Desktop (viewport width 1024px and up): the ISO editor becomes two panes.

- Left rail, 232px, sticky, full viewport height. It holds the view navigation as a grouped
  tree and, pinned at its foot, a session panel: disc name, region, size and read-only
  status, the unsaved-changes line, then the action bar (Save, Undo, Redo, Import recipe /
  patch, Export recipe, Export .xdelta, Revert all, Close).
- Content pane: the existing view card, per-view rendering unchanged (same VIEW_RENDER
  output, accordions, fields, dirty/revert vocabulary). The page column widens to 1400px
  while the ISO editor is open; the content pane caps at about 1100px and data-heavy views
  use the full pane width.
- The save editor keeps its current single column and gets only the shared chrome:
  header theme switch, boot panel, art, and small-screen strip sizing.

View groups (all 20 views from ISO_VIEWS in iso.js, each in exactly one group):

- Roster: Characters, Char names, Field models
- Items and Economy: Gear, Sets, Rune prices, Heal prices, Excel / CSV
- Combat: Spells, Runes, Passives, Enemies, Unites, MP growth, Skill effects, Balance
- System: Code patches, Assets, Changes, Reference

Below 1024px the rail is hidden and the current layout returns: chip strip in the card,
sticky action bar. The rail exists only inside the ISO editor's rendered markup.

## One DOM, relocated

The nav strip and the action bar are rendered once, with the ids and handlers they have
today (#isoTabs, data-view chips, #isoToolbar and its buttons). A matchMedia mover places
those two nodes into the rail on desktop and back into the card below 1024px. Nothing is
duplicated, so view switching, the unsaved badge, updateIsoToolbar and the test suite's
selectors all keep working in both layouts. Group headings live in the nav markup and
collapse away (display:contents) in strip mode, so the strip looks and scrolls as before.

## Per-view staged-edit counts

Each nav chip carries a count badge, shown when nonzero, in both layouts (on small screens
this is also the chip dirty marker). Counts come from the labelled edit map (isoEdits),
the same entries the save review modal already lists, attributed to a view by a registry
over the edit keys:

- Every edit key's prefix names its writing path (char, gear, gearname, gearreq, spell,
  rune, runeprice, healprice, enemy, unite, mp, skillfx, name, model, battlemodel,
  runealways, toggle, dashmul, droprates, the Balance bulk keys, the Sets keys, csv).
  A static prefix table maps each prefix to exactly one ISO view.
- Character-table edits made from the Characters view count to Characters even when the
  field belongs to a spell or rune record rendered inside that view, because the key
  prefix is the writing path, not the record type. (Rune spell-list fields inside the
  Runes view carry the rune prefix and count to Runes.)
- Imported recipes and .xdelta patches (keys imported and xdelta) have no reliable view:
  they never get an invented count. They show a dirty dot on the Changes nav item, the
  one view that can show what they changed, and the toolbar's byte/run line still gives
  the session total.
- Counts are labelled edits, not bytes: one entry per labelled change, matching the
  review list. updateIsoToolbar refreshes them, so they move with every edit, undo,
  redo, per-field revert and save (a save clears the map, so the badges clear).

## Session panel extras: Close and Revert all

Two actions the old toolbar never had, both built on the existing state machinery:

- Revert all: write the baseline bytes (ORIG, which writeISO already advances on every
  save, so revert always targets the last saved state, never a stale one) back into the
  in-memory image, clear isoEdits and both undo stacks, re-render the current view and
  refresh the toolbar. Refused as a no-op message when there is nothing to revert.
- Close: confirm when the byte diff is non-empty, then tear the session down: clear the
  disc state (ORIG, snapshots, edit map, stacks, staged-overlay flag, file and handle),
  hide the views and toolbar, and show the open card again. The last-opened disc row is
  left as it was, so reopening still works through the normal permission flow.

## Small screens (first-class, not a fallback)

The Micro 2 viewports in verification: 960x640 landscape, 480x320 landscape (the panel at
2x DPR), 320x480 portrait. At these sizes the rail stays hidden and the chip strip remains,
but when the viewport is at most 820px wide, or at most 660px tall below the desktop
breakpoint, the ISO action bar starts collapsed. A fixed strip at the bottom of the screen
carries one Actions reveal button and a mirror of the unsaved-changes pill; the bar itself
is a fixed sheet above that strip which slides up over the content when revealed, and
collapses again when a session action (Save, exports, Import, Revert all, Close) is taken.
Undo and Redo leave it open. Inside the sheet, the session-level actions (Import, Export
recipe, Export .xdelta, Revert all, Close) fold under a More toggle; Save, the unsaved
pill, Undo and Redo stay visible. Nothing is persisted: collapsed is simply the default on
every load at these sizes. Reduced motion turns the slide off.

The save editor's toolbar has a single action, so it keeps Save visible in its strip at
all sizes; only its sizing is aligned with the ISO strip.

## Embedded art

All art is local files in web/, no hotlinks. Provenance: no clean Sun Rune crest or title
art source exists in the lab's extracted disc files (the COMMON folder holds face and
effect textures, no crest), so the set is original art drawn for this editor in a Falena
dawn idiom, generated programmatically (radial sun disc, ray ring, dawn gradient) and
recorded as such. It is not official game art and is never labelled as such.

- crest.webp: the Sun Rune mark (sun disc with a ray ring), colour, alpha. Beside the
  wordmark, atop the rail, and on the boot panel. In the precache.
- crest-sil.webp: the same mark as a dark silhouette for the light theme. In the precache.
- fret.svg: a small original SVG sun-ray fretwork tile, repeated under the page header,
  the rail head and the boot panel header. In the precache.
- boot-art.webp: a dawn-over-Falena backdrop for the boot panel. Lazy (loaded by CSS when
  the boot panel shows), deliberately NOT in the precache so it never competes with the
  engine download.
- Group glyphs: monochrome original SVG motifs inline in the nav (crown for Roster, coin
  for Items and Economy, crossed swords for Combat, rune ring for System), currentColor so
  they follow the theme and the view temperature.
- PWA icons: crest-based 192, 512 and maskable 512, replacing the plain gradient disc.
- The Runes view carries the crest as a faint watermark in its header corner.

Budget: always-used art (crest, silhouette, fret, icons excluded as they replace existing
files) stays under about 90 KB total; a test pins the budget and a precache-integrity test
pins that every SHELL entry in sw.js exists on disk.

## Theme finish

- The switcher moves from the footer into the header, same two labelled buttons. Stored
  values stay twilight / sun (localStorage s5theme) so existing users keep their choice.
- First visit (no saved choice) follows prefers-color-scheme: dark gets Falena Twilight,
  light gets Sun Rune.
- Selected and primary surfaces take a gradient (deep blue to gold on Twilight, dawn gold
  on Sun). The Save button glows while unsaved changes exist.
- Colour roles never mix: gold is selection and primary actions, teal is information and
  links, crimson is errors, amber is warnings and dirty state only.
- View temperature: one data-temp attribute on the ISO content wrapper, keyed to the rail
  groups, tints section headings and borders restrainedly per group in both themes.

## Boot panel

The boot card becomes a dialogue-style panel over the dawn backdrop, crest on top, the
existing staged progress (setBoot percentages and messages) unchanged underneath. A failed
start says so in plain words ("The editor could not start. Reload to try again."), demotes
the raw error to a small details line, and offers Retry (reload) and Force refresh (the
existing service-worker reset). The success flow and the update prompt are untouched.

## Quick wins folded in

1. Save-button label: one label logic everywhere. The button reads "Save" where in-place
   writing is available and "Export recipe" where it is not, in the rail and the sheet
   alike; updateIsoToolbar never overwrites it with the other mode's wording.
2. Chip-strip affordances: a fade mask on the clipped right edge in strip mode, and the
   active chip is scrolled into view on view change.
3. ARIA: the nav is a tablist in both homes, chips are tabs with aria-selected, arrow
   keys move between them; the Actions reveal and More toggle carry aria-expanded and
   aria-controls; focus-visible outlines are preserved.
4. Status colours: errors use the crimson role colour in both themes, brightened on the
   dark theme so it reads at small sizes.

## Out of scope

- Any change to the note-fold system, model pairing, per-field revert, health checks or
  the review modal.
- A rail or two-pane layout for the save editor.
- New editing features of any kind.

## Preserved behaviour

Boot flow, undo/redo, staged vs saved separation, the paired model pickers, PWA and
service worker behaviour (network-first shell, cache-first Pyodide). Version v1.26.0,
service worker cache s5editor-v44.

## Verification

- cd web && npm test (full suite, including the new layout, counts, precache, art budget,
  theme and small-bar suites), plus npm run test:e2e.
- Screenshots in scratch/ui-v2/, local build with the truncated test disc loaded: desktop
  1440 wide, the 1024 boundary on both sides, and 960x640, 480x320, 320x480 with the
  action sheet collapsed and expanded.
- Both pristine ISO hashes re-checked after the work (US and PAL).
