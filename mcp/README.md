# Suikoden V Editor MCP server

A local Model Context Protocol (MCP) server that lets an AI tool (Claude Code,
Claude Desktop, or any MCP client) do what the web editor does: inspect and edit
a Suikoden V disc image (NTSC-U or PAL) and Suikoden V saves, with the same
staged review model, the same validation, and the same refusals.

## Architecture

Python 3, standard library only, stdio transport. The server holds no editing
logic of its own; it is a tool surface over the editor's canonical cores:

- **ISO side (Python):** every read and write goes through `Editor/s5patch.py`,
  `Editor/s5fields.py` and `Editor/s5toggles.py`, the exact modules the web
  editor runs under Pyodide. The view list is parsed out of `web/iso.js` at
  startup rather than re-typed, the Changes audit runs `Editor/s5changes.py`,
  recipes are the engine's own `.s5mod` journal (`s5patch.export_mod`), and
  the .xdelta export runs `web/vcdiff.js` through the small Node bridge
  `mcp/xdelta_bridge.mjs`. Nothing about the disc format is reimplemented
  here, so offsets cannot drift from the shipped editor.
- **Save side (Python):** every operation calls `Editor/s5save.py` directly,
  the same module the web save editor runs. All decoding, clamping, inventory
  validation and memory-card ECC work is s5save's.

Why Python as the host: S5's cores are Python (the web editor runs them under
Pyodide), so a Python host loads them in-process with no glue. The one
JavaScript core, `web/vcdiff.js`, is reached through the small Node bridge,
which is the only part that needs Node.

## Install and run

Requirements: Python 3, Node.js (only for `iso_export_xdelta`), and a checkout
of this repository. There is nothing to install: no pip or npm dependencies.

```sh
python3 /path/to/Suikoden-5-Editor/mcp/server.py
```

The server speaks MCP over stdin/stdout; run it through your AI tool, not by
hand.

## Register it in an AI tool

Claude Code:

```sh
claude mcp add suikoden-5-editor -- python3 /path/to/Suikoden-5-Editor/mcp/server.py
```

Claude Desktop (or any client that takes an `mcpServers` config):

```json
{
  "mcpServers": {
    "suikoden-5-editor": {
      "command": "python3",
      "args": ["/path/to/Suikoden-5-Editor/mcp/server.py"]
    }
  }
}
```

Replace `/path/to/Suikoden-5-Editor` with your checkout path.

## The rules the server enforces

- **Working copies only.** Opening an ISO or save copies it into `mcp/.work/`.
  The file a thing was opened from is never written, and every export refuses
  that path as its target. Outputs go only where the caller says.
- **Stage, review, commit.** ISO edits land on the working copy (the staging
  area) and are reviewed with `iso_list_changes`, which decodes them through
  the editor's own Changes engine. Save edits stage in memory and are reviewed
  with `save_list_changes`. Nothing reaches a caller-visible file until
  `iso_export_iso`, `iso_export_recipe`, `iso_export_xdelta` or `save_export`
  is called. `iso_discard_changes` and `save_discard_changes` throw a staged
  set away.
- **Editor refusals stay refused.** The toggle registry refuses a patch site
  holding bytes it does not recognise, and `iso_set_toggle` surfaces that
  refusal unchanged. Raw writes are constrained to the boot ELF's loaded
  segments, the region every named editor write lands in; overlay and
  DATA.PAK writes go through container re-encoding in the editor (including
  its HENSEI sector-fit refusal) and are not offered as raw writes. Region
  detection is the engine's own: NTSC-U (SLUS-21291) and PAL (SLES-54087),
  with the field bases rebound per region exactly as the web editor does.
- **The NG+ save control converts the save.** Staging `newGamePlus` through
  `save_stage_misc` requires the `confirmNgPlusConversion` argument. Setting
  the carry byte is not a flag flip: when the game loads the save, its
  carry-over build resets the party and every character record to the New
  Game Plus opening state (potch and world position carry over).
- **Legible errors.** Tool failures come back as plain-language MCP error
  results (what was refused and why), not stack traces.

## Tools

ISO:

| Tool | Purpose |
| --- | --- |
| `iso_open` | Open an ISO (copied to the work area, region verified by the engine) |
| `iso_list_views` | The editor's 20 views, parsed from `web/iso.js`, with record-editing marks |
| `iso_describe_view` | A record view's editable fields, from the `s5fields` registries |
| `iso_list_records` | Records of a view (characters, gear, sets, spells, runes, prices, enemies, unites, MP growth, skill effects, char names, field models) |
| `iso_get_field` / `iso_set_field` | Read or stage one field of one record (Characters takes a `table` argument, Gear a `slot` argument) |
| `iso_read_word` / `iso_stage_word` | Raw 1/2/4 byte access; writes constrained to the boot ELF's loaded segments |
| `iso_list_toggles` / `iso_set_toggle` | The Code patches registry (`Editor/s5toggles.py`, read at runtime), with state and the registry's own refusals |
| `iso_list_changes` / `iso_discard_changes` | Review (decoded by `s5changes`) or discard the working copy's change set |
| `iso_export_iso` | Write a full patched ISO copy |
| `iso_export_recipe` | Write the edits as an `.s5mod` recipe JSON, the editor's own format |
| `iso_export_xdelta` | Write a standard VCDIFF patch, built by `web/vcdiff.js` through the Node bridge |

Saves:

| Tool | Purpose |
| --- | --- |
| `save_open` | Open a memory card (.ps2/.mc2/.mcd/.bin), .psu, or individual save (.cbs, .xps/.sps); decodes every S5 save via s5save |
| `save_get` | Decoded state: summary, characters, party, inventory, recruitment, names, resources, health (the audit snapshot) |
| `save_stage_character` | Stage character edits in s5save's own slots: level, exp, SP, weapon level, armor, runes, rune uses, skills, held items, recruitment |
| `save_stage_misc` | Stage names, potch, party SP, level, party composition, inventory; `newGamePlus` only with `confirmNgPlusConversion` |
| `save_list_changes` / `save_discard_changes` | Review or discard staged save edits |
| `save_export` | Apply staged edits through s5save and write to a chosen path |

## Tests

```sh
python3 mcp/test.py
```

The harness speaks to the server as a real MCP client over stdio and runs
against fixtures: the repo's truncated US test disc
(`~/workspace/suikoden5-debug/test-disc-us-truncated.iso`) and a copy of a
real save from the workspace save library. It covers the handshake and tool
listing, ISO discovery, a field edit round trip, toggle staging and restore,
the refusal cases (raw write outside the editable region, the registry's
foreign-byte refusal, the NG+ confirm gate), every export, and the
working-copy guarantee (source hashes asserted unchanged). Optional real-data
smoke tests:

```sh
S5_ISO=/path/to/suikoden5-us.iso S5_CARD=/path/to/card.ps2 python3 mcp/test.py
```

With no `S5_ISO`, the harness also opens the checkout's own US ISO read-only
if one is present. Both smoke tests are read-path safe: the server never
writes a source file, and the harness asserts the hashes afterwards when the
env vars are set.

## Known gaps

Operations the cores could not expose without reimplementing editor logic, and
which are therefore not offered rather than faked:

- **Some ISO views are discovery-only.** Sets and Field models can be listed
  with `iso_list_records`, but their editing surfaces (set members, bonuses,
  handlers and gates; model pointer picks paired with battle-model overlay
  writes) do not reduce to the generic field tools, so `iso_get_field` and
  `iso_set_field` refuse them. Their bytes remain reachable through
  `iso_read_word` and `iso_stage_word`.
- **Battle models, Assets (DATA.PAK browsing and portrait/texture export),
  Balance (hard mode, growth and enemy scaling) and the Excel/CSV view** are
  not exposed: they run on the web editor's overlay staging and file
  hand-off machinery, or are bulk transforms over the same field writes that
  an MCP client can compose from the field tools.
- **The Passives view** (always-on rune gates) is not exposed: on this branch
  it still runs on its own gate path in `s5patch`, separate from the toggle
  registry. Its five runes are expected to join the registry when the
  Passives merge lands, at which point they flow through `iso_list_toggles`
  and `iso_set_toggle` with no server change.
- **The health check's verdicts** are not re-run: `save_get` with section
  `health` returns the audit snapshot from `s5save`, but the rules live in
  the web editor's `web/health-core.js` and are not duplicated here.
- **Starting loadouts** (the Characters view's starting runes and skill
  ranks) are NTSC-U only in the engine and are not exposed as field views;
  the per-character tables that are exposed are the ones in `s5fields.TABLES`.
- One ISO and one save are open at a time, matching the web editor's session
  model.
