# bsb_tables.tsv — Source Data Errors Found

Data-entry errors found in `bsb_tables.tsv` (the Berean Bible interlinear
export, from berean.bible/downloads.htm) while building an import pipeline
against it. These are errors in the source file itself, not in our
processing of it — worth reporting upstream. Everything below was verified
against the actual published BSB text, not just inferred from the file's
own internal patterns.

## 1. Cross-reference column holds paragraph-formatting markup instead of a cross-reference

Three rows have `<p class=|...|>`-style paragraph/indent markup in the
**Crossref** column, where every other row in the file (1,325 of them)
correctly has a real cross-reference there instead (shaped like `<br
/><span class=|cross|>(<a href=...>John 1:1-5</a>...)</span>`). This
content belongs in the **Par** column, not Crossref.

| Verse | Crossref column currently contains | Should be in Par instead |
|---|---|---|
| Proverbs 26:24 | `<p class=|indent1|>` | (moved to Par) |
| Jeremiah 38:7 | `<p class=|reg|>` | (moved to Par) |
| Matthew 25:16 | `<p class=|red|>` | (moved to Par) |

## 2. Leftover ". . ." continuation marker glued to real content

Eleven rows have the file's own ". . ." continuation-marker convention (used
elsewhere to mean "this word's translation is already covered by a nearby
word — render nothing") sitting in the same cell as real, distinct content,
instead of being on its own or removed entirely. Confirmed against the
published BSB text: none of these eleven verses actually contain an
ellipsis.

| Verse | Hebrew/Greek word | BSB version column currently contains | Should be |
|---|---|---|---|
| Numbers 26:18 | אַרְבָּעִים | `. . . 40,500` | `40,500` |
| Numbers 26:22 | שִׁשָּׁה | `. . . 76,500` | `76,500` |
| Numbers 26:25 | אַרְבָּעָה | `. . . 64,300` | `64,300` |
| Numbers 26:27 | שִׁשִּׁים | `. . . 60,500` | `60,500` |
| Numbers 26:34 | שְׁנַיִם | `. . . 52,700` | `52,700` |
| Numbers 26:37 | שְׁנַיִם | `. . . 32,500` | `32,500` |
| Numbers 26:41 | חֲמִשָּׁה | `. . . 45,600` | `45,600` |
| Numbers 26:43 | אַרְבָּעָה | `. . . 64,400` | `64,400` |
| Numbers 26:47 | שְׁלֹשָׁה | `. . . 53,400` | `53,400` |
| Numbers 26:50 | חֲמִשָּׁה | `. . . 45,400` | `45,400` |
| John 21:7 | γυμνός | `. . . )` | `)` |

Note: **Ephesians 3:14** looks similar at a glance (its BSB version column
starts with `...`) but is a genuinely different case — a bare, unspaced
ellipsis (`...`, not our target's spaced `. . .`), and it *does* have a real
ellipsis in the published text (Paul's sentence resuming after the long
digression since 3:1). Not an error, not included above.

## 3. BegQ column holds the website's own verse-number anchor markup, not a quote mark

116 rows have the exact same literal value in the **BegQ** (beginning
quote) column: `<span class=|reftext|><a href=|#|><b>1</b></a></span>`.
This isn't quote-mark content at all — it's berean.bible's own inline
"1" verse-number link, and it always lands on a Psalm's first content
verse (the explicit "1" the site shows because that psalm's superscription
line, e.g. "A Psalm of David", isn't itself counted as verse 1 — unlike
psalms with no superscription, where no such marker appears). Confirmed:
always this one exact string, always in BegQ, never combined with real
quote-mark content. A few examples (of 116 affected psalms, spanning
Psalm 3 through Psalm 145):

| Verse | BegQ column currently contains |
|---|---|
| Psalm 14:1 | `<span class=\|reftext\|><a href=\|#\|><b>1</b></a></span>` |
| Psalm 51:1 | `<span class=\|reftext\|><a href=\|#\|><b>1</b></a></span>` |
| Psalm 121:1 | `<span class=\|reftext\|><a href=\|#\|><b>1</b></a></span>` |

## 4. Minor: stray "z" in the Language column

One reserved/blank filler row (in the buffer between Numbers 7:59 and the
next verse) has `z` in the Language column instead of being empty like its
neighboring filler rows. Harmless — it's a content-free row either way — but
noted for completeness.

## Resolved lead: 1 Samuel 1:1's "Ramathaim-zophim" (not a source error)

An earlier revision of this doc flagged "Ramathaim-zophim" rendering as
two separate, identically-labeled lemma annotations as a possible
`bsb_tables.tsv` data issue. Investigated further and ruled out: the
`vvv`/`. . .` continuation markers `import_bsb_table.py` already parses
are working exactly as intended — two Hebrew tokens correctly sharing one
compound Strong's number, exactly like "Bethel," "Ben-hadad," and ~150-200
other genuine compound names throughout the Bible. The actual bug was on
the rendering side: `table_composer.py` was showing that shared compound
lemma on every member token, rather than falling back to each token's own
real form the way it already does everywhere else. Fixed in `1.1.5` (see
CHANGELOG.md and `utils/scan_compound_strongs.py`'s module docstring for
the full investigation trail, including the wrong turns that ruled out
simpler-looking fixes).

---

*Compiled while building an import pipeline for `intralinear-bible`
(github.com/scottrbailey/intralinear-bible). Happy to share more detail on
any of these if useful for tracking down the fix.*
