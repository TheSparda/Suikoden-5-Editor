# Suikoden V Editor — field reference

What every editable value in the ISO editor actually means: its scale, its limits, and the
traps that make a perfectly normal number look like a bug. Written for people modding the
disc, whether through the editor or a hex editor.

The short version of everything below: **ISO edits apply to a NEW GAME.** They change the
game's data tables, and the game copies those tables into your save when a playthrough
starts. Editing a character's starting HP does nothing to a character who already exists in
a save. Use the Save editor for an in-progress game.

**Contents**

- [Rules that apply to every table](#rules-that-apply-to-every-table)
- [The three grade scales](#the-three-grade-scales)
- [Characters](#characters)
- [Skills and skill effects](#skills-and-skill-effects)
- [Enemies](#enemies)
- [Gear](#gear)
- [Sets](#sets)
- [Spells](#spells)
- [Runes, passives, and the Dawn Rune](#runes-passives-and-the-dawn-rune)
- [Prices](#prices)
- [Unites](#unites)
- [MP growth](#mp-growth)
- [Balance (the scalers)](#balance-the-scalers)
- [Excel / CSV round-trip](#excel--csv-round-trip)
- [Char names, Field models, Assets, Reference](#char-names-field-models-assets-reference)
- [The Save editor](#the-save-editor)
- [What is deliberately not editable](#what-is-deliberately-not-editable)
- [Appendix: table addresses](#appendix-table-addresses)

---

## Rules that apply to every table

**Edits land on a new game.** Every table in the ISO editor is game *data*. The only things
that touch an existing playthrough are in the Save editor.

**Numbers have a fixed width, and the width is the ceiling.** Each field is a u8 (0-255), a
u16 (0-65535), a u32, or a signed s8 (-128 to 127). The two edit paths differ on purpose:

- Editing a field in a panel **refuses** a value that doesn't fit, so a typo can't silently
  wrap to something absurd. Each field shows its width as a pill (`1B`, `2B`, …) and the input
  carries the matching bounds, so you can see the ceiling before you hit it.
- A CSV import **caps** instead, and the import report names every value it capped. That's
  so a sheet-wide "multiply this column by 3" doesn't fail halfway and leave the table in a
  half-applied state.

**Nothing is one-way.** Every field has a revert button, the panel has undo/redo, and a
backup of the ISO is made before the first write unless you turn that off. The two scalers
(Balance) keep a separate baseline file next to the ISO so **Restore** is exact rather than
approximate. Mod recipes (Export/Import) capture your edits as a list of offsets and values
you can re-apply to a fresh disc.

**Both regions work.** NTSC-U (SLUS-21291) and PAL (SLES-54087) hold byte-identical data;
only the table addresses move, plus one stride difference in the rune-price table. The
editor detects the region from the disc serial and rebinds the addresses. Both sets are in
the [appendix](#appendix-table-addresses).

**Verified vs inferred.** Most of what follows was pinned by matching the bytes against a
known in-game value or a community guide, and the source comments say which. Where a field's
meaning is genuinely unconfirmed, this document says so rather than guessing. Where something
is marked unverified, the write is still bounded to that field — it can't damage a neighbour —
but the in-game effect isn't proven.

---

## The three grade scales

Suikoden V uses three different letter-grade encodings, and they do not line up. Mixing them
up is the single easiest way to write a wrong value.

| Scale | Used by | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|---|
| **Skill rank** | Character equipable-skill caps, save-file skill ranks | — (none) | E | D | C | B | A | S | SS |
| **Character affinity** | Character elemental affinities, set-bonus affinity writes | None | E | D | C | B | A | S | — |
| **Enemy affinity** | Enemy elemental affinities | E | D | C | B | A | S | — | — |

So a `5` is **A** on either character scale but **S** on an enemy, and a `6` is **S** on a
character while an enemy has no such value at all. The editor shows the right letters per
table; a hex editor won't.

Separately, the **skill effect** table has rank *columns* E / D / C / B / A / S / SS — seven
values per row, no "none" column. See below.

---

## Characters

Everything here is indexed by character id. The playable roster and its ids are in
`Editor/s5_characters.json`; ids not in that list are NPCs and unused slots that still have
records on the disc.

### Stats and growth

Eighteen bytes per character: nine starting stats, then nine growth values.

| Field | Width | Meaning |
|---|---|---|
| HP, Attack, Technique, Magic, Evasion, PDF, MDF, Speed, Luck | u8 each | The character's stats at **level 1, on a new game**. |
| …the same nine again, as "Growth" | u8 each | How much that stat gains per level-up. |

PDF is physical defense, MDF is magic defense. Verified byte-for-byte against the game's own
stat data (Dinn and Lance match exactly).

Two things to watch:

- These are u8, so **255 is the hard ceiling** on a starting stat and on a growth value. If
  you want a monster character, growth is the lever, not the starting stat.
- Characters have **no Accuracy stat**. Enemies do. The two stat blocks are not the same
  shape, so don't copy a column from one sheet to the other.

### Elemental affinities

Fourteen bytes, one per element, in this order:

> Sun, Fire, Lightning, Wind, Water, Earth, Star, Sound, Holy, Dark, Slash, Thrust, Punch, Shoot

Each uses the **character affinity** scale (0 = None, 6 = S). Verified in game: the Prince has
Sun A, Zerase has Fire/Star/Dark A, Lance has Punch A.

### Equipable skill caps

Forty-eight bytes, one per skill, on the **skill rank** scale (0 = can't equip it at all,
7 = SS).

This is a **cap, not a current rank**. It's the highest rank that character is ever allowed
to train the skill to; they still start unlearned and spend skill points to climb. Raising a
cap doesn't grant the skill, it permits it. The panel has presets to max or clear a whole
character's array at once.

Slot 26 in this list is an unused skill (`??? (unused)`) — see the skill-effects section, where
its two rows also read as unknown.

The 49th byte of each record is padding and isn't exposed.

### Weapon growth

Sixteen u8 values: the attack power of that character's weapon at sharpen level 1 through 16,
ascending. This is the weapon's own damage curve, not a stat bonus.

### Starting equipment and starting items

Both live in the same record.

- **Starting equipment** — four armor ids: Head, Body, Arm (gloves), Feet. `0` means nothing
  equipped. The ids are the game's armor ids, which the editor offers as name dropdowns.
  Ground truth: Richard starts in the full knight set.
- **Starting items** — four held-item slots, stored as pointers into the game's item-name
  pool rather than as ids. Because a wrong pointer would be a real corruption, the editor only
  lets you choose from the closed set of pointers the game already uses for held items.
  Ground truth: the Prince starts with a Lightning Amulet; Richard with a Sun Badge and a
  Jewel Necklace.

---

## Skills and skill effects

This is the table most likely to look broken when it isn't.

### A row is a sub-effect, not a skill

There are 165 rows and only 48 skills. A skill owns a **run of consecutive rows**, one per
sub-effect it grants. *Defense* is four rows: `Defense +`, `% Block`, `% Parry`,
`% Weapon Defense`.

The row order is exactly the skill order from the equipable-skill caps list — 48 skills, 48
groups, one-to-one:

| Skill | Rows | Sub-effects |
|---|---|---|
| Stamina | 0 | % HP |
| Attack | 1 | Attack + |
| Defense | 2-5 | Defense +; % Block; % Parry; % Weapon Defense |
| Technique | 6-9 | Technique +; % Counter; % Ignore Def; % Thrust Back |
| Vitality | 10-12 | % Critical; % Double Critical; % Charge |
| Agility | 13-16 | Speed +; Evasion +; % Multiple Swing; % Continuous Attack |
| Magic | 17 | Magic + |
| Magic Defense | 18-19 | Magic Defense +; % Resist MAG damage |
| Incantation | 20-21 | % reduced casting time; % chance to turn a single-target spell into an area spell |
| Sword of Magic | 22 | Sword Magic + |
| Raging Lion | 23-24 | % HP; Attack + |
| Fate Control | 25-29 | Attack +; Tech +; % Counter; % Ignore Def; % Thrust Back |
| Karmic Effect | 30-37 | Defense +; % Block; % Parry; % Weapon Defense; Tech +; % Counter; % Ignore Def; % Thrust Back |
| Armor of Gods | 38-43 | Defense +; % Block; % Parry; % Weapon Defense; MAG DEF +; % Resist MAG damage |
| Swift Foot | 44-51 | TECH +; % Counter; % Ignore Def; % Thrust Back; Speed +; Evasion +; % Multiple Swing; % Continuous Attack |
| Triple Harmony | 52-59 | Attack +; % Crit; % Double Crit; % Charge; Speed +; Evasion +; % Multiple Swing; % Continuous Attack |
| All-out Strike | 60-68 | % Crit; % Double Crit; % Charge; Speed +; Evasion +; % Multiple Swing; % Continuous Attack; % casting time; % area spell |
| Untold Clarity | 69-84 | Attack +; Defense +; % Block; % Parry; % Weapon Defense; Technique +; % Counter; % Ignore Def; % Thrust Back; % Crit; % Double Crit; % Charge; Speed +; Evasion +; % Multiple Swing; % Continuous Attack |
| Divine Right | 85-87 | Magic +; % casting time; % area spell |
| Zen Sword | 88-91 | Attack +; % casting time; % area spell; Sword Magic + |
| Sacred Oath | 92-97 | Magic +; Magic Def +; % Resist Magic damage; % casting time; % area spell; Sword Magic + |
| Royal Paradise | 98-120 | all 23 effects, in canonical order |
| Thief | 121-122 | % chance of success; % Potch steal |
| Mow Down | 123-124 | % active; number of targets |
| Pierce | 125-126 | % active; + enemies |
| Freeze | 127-128 | % active; added % damage |
| *(unused skill slot)* | 129-130 | unknown; unknown |
| Barrage | 131-132 | % active; + enemies |
| Long Throw | 133-134 | % active; + enemies |
| Dragon Special | 135-136 | % active; number of targets |
| Forge | 137-138 | weapon level it can sharpen; unknown |
| Combat Teacher | 139-140 | (main); rank a skill can be taught to |
| Chain Magic | 141 | % repeat magic casting |
| Analyze | 142 | reveals enemy status |
| Potch Finder | 143 | % potch increase from battles |
| Treasure Hunt | 144-145 | % war trophy after battles; unknown |
| Escape Route | 146 | % escape from normal battles |
| Healing | 147 | restore % HP between rounds |
| Treatment | 148 | restore % HP after battle |
| Haggle | 149-150 | % off shop prices; % more when selling |
| Trade In | 151 | % trade-in price |
| Cook | 152-153 | % ingredient drops; % ingredient drops |
| Rune Sage | 154 | (main) |
| Bard | 155 | alters battle music |
| Perfect Pitch | 156-160 | DoReMi Elf ATK / DEF / Accuracy / MDF / HP % |
| Appraisal | 161 | (main) |
| Bath | 162 | % Toasty during battle |
| Tutor | 163-164 | (main); rank a skill can be taught to |

Rows 129-130 line up exactly with the unused skill at slot 26 of the caps list, and they have
the same two-row `% active` + count shape as Pierce and Barrage on either side of them.

Every group lists its effects in one fixed canonical order (the order Royal Paradise spells
out, since it grants all 23). That invariant is asserted by the test suite, because it's what
caught a mislabeled row: `Sword Magic +` was shown as row 20 when it is really row 22, with
the two Incantation effects at 20 and 21.

### The columns

Seven values per row: the magnitude at rank **E, D, C, B, A, S, SS**. Each is a u16.

### Why a row starts with zeros

Plenty of rows read `0, 0, 110, 115, 118, 120, 130`. That does **not** mean the skill is
inactive at E and D. It means *that sub-effect* doesn't unlock until C. The skill itself is
live at E through its other rows — `Defense +` pays out 3 at rank E while `% Block` is still 0.

So editing one of those zeros is a real change, not a write into dead data. If you want Block
at rank E, put a value there.

### Read each row's scale off its own values, not its name

The table mixes three numeric conventions, and the `%` in a label doesn't tell you which:

| Convention | How to spot it | Examples | Editing a 0 |
|---|---|---|---|
| **100-based** | Live values sit in the 1xx range | `% Block` 110-130, `Stamina (% HP)` 105-130, the `Sword Magic +` rows 102-120 | Use 101-109, matching the curve `% Critical` uses (101, 104, 108, …) |
| **Direct amount** | Values climb from 0/2/5 into the tens | `Attack +` 5-40, `Thief (% chance of success)` 0-50, `Freeze (% active)` 2-35 | Use a small number continuing the curve, e.g. 1 or 2 |
| **Literal count** | Tiny integers, often ending at 65535 | `Mow Down (number target)` 1,1,2,2,3,4,65535 | It's a count of enemies; 65535 means "all of them" |

Putting 101 in a count row, or 10 in a 100-based row, is the mistake this table invites. On a
100-based row, 100 is the neutral point and nothing the game ships is between 1 and 99.

### Scope

This table is **global**. It is indexed by sub-effect, not by character, so a change applies to
every unit that has the skill.

---

## Enemies

219 records (ids 0-218). Id 0 is a dummy; 1-218 are the named roster, ending at Bahram.
Anything past 218 is not an enemy — the editor used to scan further and list rows of unrelated
executable data as `Enemy 221`, `Enemy 222` and so on, which is where those impossible
five-digit stats and level-143 rows came from. Fixed in v1.17.1.

Each record is 0x7C bytes:

| Field | Offset | Width | Notes |
|---|---|---|---|
| Level | +0x01 | u8 | |
| HP | +0x02 | u16 | |
| Attack | +0x04 | u16 | |
| Technique | +0x06 | u16 | |
| Accuracy | +0x08 | u16 | Enemies have this; characters don't |
| Magic | +0x0A | u16 | |
| Evasion | +0x0C | u16 | |
| PDF | +0x0E | u16 | Physical defense |
| MDF | +0x10 | u16 | Magic defense |
| Speed | +0x12 | u16 | |
| Luck | +0x14 | u16 | |
| Potch reward | +0x16 | u16 | Money dropped |
| Skill Pts reward | +0x18 | u16 | |
| 14 elemental affinities | +0x1A … +0x27 | u8 each | **Enemy affinity scale: 0 = E … 5 = S**, in the same element order as characters |
| 100% drop flag | +0x28 | u8 | `0x0F` makes the enemy drop its whole loot table every time |
| Drop 40% | +0x2C | u16 | |
| Drop 20% | +0x34 | u16 | |
| Drop 10% | +0x3C | u16 | |
| Drop 5% | +0x44 | u16 | |
| Drop 1% | +0x4C | u16 | |

Every stat is a u16, so **65535 is the ceiling** on a modded enemy. The editor caps at it
rather than refusing the write when importing a sheet.

### Drop slots

Each drop slot is really 8 bytes: a category byte, an item byte, then six bytes the editor
doesn't touch. Read as one little-endian u16, the value is `category | item << 8`, which is why
the editor shows a single dropdown per slot. The categories:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| None | Head Gear | Body Gear | Arm Gear | Foot Gear | Accessories | Orbs | Rune Pieces | Key Items | Healing Items | Scrolls | Party Items |

Ground truth for the whole layout: Nariqua (id 4) is Lv45, 1800 HP, 2500 potch, 135 skill
points, with a 20% drop of category 7 item 0x10 = a Drain Piece. Holly Boy (id 1) is Lv10,
80 HP, 30 potch, 10 skill points.

---

## Gear

Five tables sharing one record layout: Head (32), Body (71), Arm (37), Foot (30), Accessory
(49). Verified against the Suikosource Armor List guide.

| Field | Width | Notes |
|---|---|---|
| Buy price / Sell price | u32 | Independent values; the game's own data keeps sell = buy / 2 but nothing enforces it |
| DEF | u8, unsigned | |
| Type | u8 | Weight class: 1 = Light, 2 = Medium, 3 = Heavy. Accessories use a different set (1 = Cape … 6 = Ring) |
| SPD penalty | u8, unsigned | The speed cost of wearing it; scales with weight class |
| HP, Attack, Technique, Magic, Evasion, MDEF, Speed, Luck | **s8, signed** | Signed on purpose — armor can carry a penalty |
| Auto-heal %, HP drain %, Critical %, Status resist %, Potch %, Counter % | u8 | Writing a non-zero value **adds** that effect to the piece |
| Short / Mid / Long-range ATK, Short / Mid / Long-range ACC | u8 | Range-specific bonuses (bangles, bracers, power gloves) |
| 14 × element ATK, 14 × element DEF | s8 each | Same element order as everywhere else |

### Name and description

A piece's **name** and its **description** (the in-game "what it does" line) are both real
strings on the disc, and both are editable in English.

They're fixed-length, so each is capped to the room the existing string actually has: its own
bytes plus the run of padding after it. The padding counts, so a description you shorten can be
lengthened again later. The cap is measured per record rather than assumed, because 15 of 219
armor records hold real data immediately after their description.

The description is **not derived from the stats**. Adding a Counter % to a helmet works, but
the line still describes the original piece until you rewrite it yourself. The editor's own
English labels come from a curated list, so editing the disc string changes what the *game*
shows, not what the editor calls the item.

One effect isn't a number anywhere: **element resist / attribute flavor** (a piece that grants
Fire resistance as a property rather than as an element DEF value). Records that differ only by
that effect are otherwise byte-identical, so it's shown from the description text and never
written as a value.

---

## Sets

Wearing a full set grants a bonus. Unlike everything else in this document, set bonuses aren't
table data — they're inline code — but every value that matters is an immediate, so all of it
is editable: which pieces the set requires, the magnitude of the bonus, and which bonus a set
uses at all (a jump-table repoint). You can also assemble a **custom** bonus into verified
free space, which adds effects rather than only retuning existing ones.

The "Prince only" restriction on the Sun set is a character check you can switch off, so the
bonus applies to whoever wears the full set.

Effect targets marked *inferred* sit inside a block that was verified as a whole, but weren't
individually proven. The verified ones: HP, Attack, Magic Defense, Speed, Critical %, Double
critical %, and Water affinity — the last pinned by the Fish set, whose handler writes 6
(= rank S) and whose documented bonus is "Water affinity is S".

---

## Spells

106 spells, each with four editable fields.

| Field | Width | Notes |
|---|---|---|
| Element | u8 | 0 = Sun/Special, 1 = Fire, 2 = Lightning, 3 = Wind, 4 = Water, 5 = Earth, 6 = Star, 7 = Sound, 8 = Holy, 9 = Dark, 0xA = Slash, 0xB = Thrust, 0xC = Punch, 0xD = Shoot |
| Power / heal | u16 | Damage, or healing on a healing spell. **9999 means a full heal** |
| Target | u8 | 1 = Transform, 2 = Single (ally), 3 = Self, 4 = Single (enemy), 0x0A = All (ally), 0x0C = All (enemy), 0x14 = Column, 0x24 = Row, 0x44 = Cluster |
| Status | u8 | 0 = None, 2 = Revive, 0x20 = applies a status/buff, 1 = undocumented |

The Status byte was checked across all 106 spells: `0x02` is exactly the three revive spells
(Light of Day, Mother Ocean, Yell), and `0x20` is the 31 status/buff appliers. The `0x01` on
four spells (First Ray, Thunder Runner, Furious Blow, Comet) is not understood.

A byte at +1 climbs steadily within each element but has no established meaning, so it's
hidden rather than shown as something it might not be.

---

## Runes, passives, and the Dawn Rune

### Rune → spell grants

26 records. A rune teaches a **contiguous run** of spells: `Start spell` through
`Start spell + Spell count − 1`, one per rune level. So the count is also how many levels the
rune has. The spell ids index the Spells table above.

Verified 26/26 against the rune guide. Runes with no grant record of their own (fixed spell
sets) are shown for completeness but have nothing on the disc to write back.

### The Dawn Rune

The Dawn Rune is the one rune whose spell count is **not** read from its own record. The
game's list builder special-cases start-spell-id 0 and reads a counter byte that the story
script lowers as the plot advances. That's why editing the record does nothing, and why the
community fix has to rewrite the value every frame.

The editor patches the setter instead: one instruction in the wrapper that writes that
counter, so it always stores the number you pick. Found by signature, so it works in both
regions, and it's fully reversible.

### Passives ("always-on" rune effects)

85 of the 173 call sites that ask "does this character have this rune equipped" share one gate
shape: check the rune, then check the rune's own enable flag. Both branches land on the same
"inactive" path, so NOPing the pair makes the effect apply unconditionally, with no rune
equipped. Eight bytes per effect, reversible, and the editor remembers the original
instructions so the toggle restores them exactly.

---

## Prices

Three separate tables, because the game stores them separately:

- **Prices** — 148 item/equipment records, buy and sell as u32.
- **Rune prices** — 70 orb records. Buy and sell are 3-byte little-endian values.
  **PAL uses a different stride here** (80 rather than 76); the editor handles it.
- **Heal prices** — 41 healing-item records, same 3-byte layout.

Sell is conventionally half of buy, but the two are independent fields — set them however you
like. Event-only orbs ship with a buy price of 0.

---

## Unites

All 49 unite attacks, verified against the Unites guide. The records are **packed and
variable-length**: a name, a description, a participant count, then that many character ids,
then straight into the next record.

That means the **member count is not editable** — changing it would shift every record after
it. The participant ids are editable in place, so you can swap who takes part. Damage and
target are engine-driven and shown for reference only.

The DoReMi quintuplets use character ids 129-133, which are outside the normal roster.

---

## MP growth

Four groups — magic Level 1 through 4 — each holding nine u16 MP thresholds: the MP cost as a
caster gains more casts of that level. Verified content, e.g. Lv1 is
`0, 20, 40, 75, 100, 130, 165, 225, 290`.

This tunes what each cast costs. It **cannot** raise the 9/9/7/5 casts-per-level cap, which
lives elsewhere.

---

## Balance (the scalers)

Two bulk operations. Both store the original values in a sidecar file next to the ISO on first
apply, so re-applying scales the *originals* rather than compounding, and **Restore** is exact.

- **Characters** — multiply every character's starting stats by a factor. A quick hard mode.
- **Enemies** — multiply every enemy's combat stats (HP, Attack, Technique, Accuracy, Magic,
  Evasion, PDF, MDF, Speed, Luck), with a separate multiplier for HP so you can make enemies
  tankier without making them hit harder. Potch, skill-point rewards, affinities and drops are
  deliberately left alone: those change the economy, not the difficulty.

Anything past a field's ceiling is capped rather than failing, and the report counts how many
values hit the cap. Tripling a 30,000 HP boss lands on 65535.

If you scaled enemies with a build before v1.17.1, the scaler also repairs it: those runs
wrote into data past the end of the enemy table, and the sidecar still holds the original
bytes, so the next apply puts them back and says how many slots it fixed.

---

## Excel / CSV round-trip

Export a table, bulk-edit it in a spreadsheet, import it back. Available for character stats,
affinities, skill caps, weapon growth and starting equipment; enemies; spells; runes; prices;
skill effects; and MP growth.

How the import behaves:

- The **id column identifies the row.** Rows are matched by id, not by position, so sorting or
  filtering the sheet is safe.
- An **empty cell is skipped**, not written as 0.
- Values that don't fit the field are **capped**, and every cap is listed in the report.
- Importing a sheet into the **wrong table is refused** with a message naming the table the
  columns actually belong to. (Previously, the enemy sheet dropped into the character table
  would write hundreds of enemy numbers into u8 character stats before erroring out.)
- An id outside the table is **rejected** rather than written.
- A UTF-8 BOM, which is what Excel writes, is accepted.

Each export carries a legend for columns that hold a code rather than a plain number — the
element/target/status maps, the drop categories — generated from the same data the pickers
use, so it can't drift.

---

## Char names, Field models, Assets, Reference

- **Char names** — the 7-character roster/menu name table. This changes the name in menus,
  party and status screens on a new game. It does **not** rewrite dialogue, cutscene or battle
  text, which live elsewhere, so a rename can read inconsistently in story scenes. The hero's
  name is chosen by the player at game start.
- **Field models** — which model file a character id loads in towns and on the field. The
  loader is a single pointer read, so swapping a model is a pointer repoint, and Reset restores
  it byte-for-byte. Characters with several looks are grouped so all of them move together.
- **Assets** — a read-only browser for the ~7,700 files packed into `DATA.PAK`. It reads from
  your file on demand and never writes.
- **Reference** — the name lists (characters, spells, skills, runes, enemies, items) the
  editor uses for its dropdowns, browsable.

---

## The Save editor

For an in-progress playthrough. Reads PS2 memory-card images and `.psu` exports.

There is **no software checksum** over the save body — that was established by reversing the
game's save I/O and its save-select validator, which only checks three fixed header constants.
Integrity comes from the card's hardware ECC, which the editor recomputes on every write. So
editing a verified field is safe without recomputing anything.

Verified and editable:

| Field | Notes |
|---|---|
| Hero name, Castle name, Army name | Written in both places the game keeps them, so the rename survives a reload |
| Potch | Capped at 99,999,999 |
| Party SP | Shared party skill points, capped at 999,999 |
| Level | Lead character, 1-99 |
| New Game Plus flag | 1 = cleared/NG+ |
| Active party | 10 slots (6 battle + 4 support); slot 0 is the hero |
| Per character | Equipped armor (4 slots + accessory), equipped runes (head / right / left), skill ranks |

Playtime is shown read-only — it's a display cache.

The project's rule is that unverified save fields don't get written, so anything not on that
list is deliberately absent rather than forgotten.

---

## What is deliberately not editable

Honest list of the gaps, so you know where the map ends:

- **Armor element resist / attribute flavor** isn't stored as a clean byte — records that
  differ only by that effect are otherwise identical — so it stays read-only.
- **Gear names and descriptions** are editable, but capped to the bytes the existing string
  occupies plus its padding; there's nowhere to grow a string that's already at its limit.
- **Unite member counts** can't change without shifting every later record.
- **Casts per magic level** (9/9/7/5) isn't in the MP table.
- **Two bytes per spell record** vary meaningfully but aren't understood, so they're hidden.
- **22 bytes of each skill-effect record** are unmapped; only the seven rank values are used.
- **Six bytes of each enemy drop slot** are unmapped beyond the category and item bytes.
- **A per-character `+0x54` value** was once shown as "Starting Potch". That was wrong — it's
  one of six unlabeled growth-tier values shared across bands of unrelated units, characters
  and enemies alike. Its meaning is unverified, so it's omitted.
- **Skill-effect rows 129-130** belong to the unused skill slot and have no confirmed meaning.

---

## Appendix: table addresses

ISO file offsets, for hex editors. The data is byte-identical between regions; only the bases
move (and the rune-price stride).

| Table | NTSC-U | PAL | Stride | Records |
|---|---|---|---|---|
| Character stats + growth | 0x48A970 | 0x48FB60 | 0x12 | one per character id |
| Character affinities | 0x48B530 | 0x490720 | 0x0E | one per character id |
| Equipable skill caps | 0x4B2731 | 0x4B80D1 | 0x31 | 113 |
| Weapon growth | 0x4987C0 | 0x49D9B0 | 0x60 | 376 |
| Starting equipment / items | 0x493112 | 0x498302 | 0x18 | one per character id |
| Enemies | 0x49F0DC | 0x4A42CC | 0x7C | 219 |
| Skill effects | 0x4AEB1C | 0x4B44BC | 0x24 | 165 |
| Spells | 0x4E87F0 | 0x4FC950 | 0x54 | 106 |
| Rune → spell grants | 0x4E6D16 | 0x4FAE76 | 0x46 | 26 |
| Item prices | 0x49433C | 0x49952C | 0x94 | 148 |
| Rune (orb) prices | 0x4E24FC | 0x4F0B20 | 0x4C (PAL 0x50) | 70 |
| Healing-item prices | 0x4CCFD0 | 0x4D2980 | 0x58 | 41 |
| MP growth | 0x4986C0 | 0x49D8B0 | 0x14 | 4 groups |
| Unites | 0x4D3420 | 0x4D8DD0 | packed | 49 |
| Gear — head | 0x495D88 | 0x49AF70 | 0x94 | 32 |
| Gear — body | 0x48C9A8 | 0x491B90 | 0x94 | 71 |
| Gear — arm | 0x4942A8 | 0x499490 | 0x94 | 37 |
| Gear — foot | 0x4974C8 | 0x49C6B0 | 0x94 | 30 |
| Gear — accessory | 0x4AC6D8 | 0x4B2070 | 0x94 | 49 |

The disc serial at 0x828BD identifies the region: `SLUS_212.91` for NTSC-U, `SLES_540.87` for
PAL.

Per-record field offsets are in the tables above for enemies, spells and gear; for the rest,
the authoritative layout is `Editor/s5fields.py`, which carries the verification notes for
every base, stride and field.
