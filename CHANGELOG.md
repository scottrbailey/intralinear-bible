# Changelog

## [Unreleased] — feature/esword-reverse-interlinear

### Added
- **e-Sword reverse interlinear** (`.bbli`): English on top, source script / transliteration / Strong's / morphology below each word, using custom `<qi>`, `<e>`, `<lem>` tags styled via the `Mods` CSS table
- **MySword interlinear stacked** variant: original script stacked under transliteration using `<ruby>/<rt>` tags with `inline-flex; flex-direction: column-reverse` — works for both Hebrew and Greek
- **Single `config.yaml`**: replaced per-format config files; `--format` and `--mode` are now CLI arguments
- **`--mode` aliases**: `intra` and `inter` accepted in addition to full names
- **Verse preview**: first verse of each testament is pretty-printed during processing; MySword also applies VerseRules transforms and shows both intralinear and stacked output
- **Version threading**: `version` from config passed through to all writer `Details` tables
- **Separate H/G VerseRules**: intralinear rules match `H` and `G` Strong's prefixes separately for independent coloring

### Fixed
- Strong's suffixes (e.g. `H871a`) stripped before normalization so dictionary links resolve correctly
- e-Sword `<q>` tag renamed to `<qi>` to avoid conflict with e-Sword's built-in block styling
- e-Sword `<heb>`/`<grk>` renamed to `<hs>`/`<gs>` to avoid built-in strikethrough styling
- `<num>`/`<tvm>` spacing: e-Sword replaces these with `<sup>` elements; targeted via `lem sup` in CSS
- Doubled tail text in verse preview (ET.tostring already includes tail)
- MySword VerseRules preview: `$N` capture references converted to `\N` for Python `re.sub`
