"""
main.py — Intralinear Bible module builder

Usage:
    python main.py [config.yaml] [--format FORMAT] [--mode MODE] [--composer SOURCE]

    --format    esword     e-Sword LT (.bbli)          [default]
                mysword    MySword (.bbl.mybible)
                osis       OSIS XML
                all        build every output target in one pass

    --mode      intralinear   BTB-L1/L2/L3 (beginner tiers), all three
                              together                              [default]
                L1/L2/L3      one BTB tier alone
                interlinear   forward interlinear: source words in their own
                              reading order, English glossed below. ROUGH
                              DRAFT -- layout/CSS not settled, table composer
                              only (AlignmentComposer's SOURCE_TO_TARGET join
                              isn't implemented yet)
                reverse       reverse interlinear: English-primary columns,
                              source words below
                Ignored when --format=all (fixed target-to-source set).

    --composer  alignment  live join across macula-hebrew/macula-greek/
                           Alignments
                table      read table_db (built by utils/import_bsb_table.py)
                           instead
                drash      table_db, but preferring tokens.english_restored
                           (utils/build_restored_names.py) over tokens.english
                           -- DTB (Drash Transliterated Bible) instead of BTB.
                           Requires table_db to have been built with
                           build_restored_names.py already run against it.
                           Only --mode intralinear/L1/L2/L3 support it so far
                           (--format esword/mysword only, not osis/all).
                Default: config.yaml's "composer" key if set, otherwise
                auto-detected from whether table_db exists on disk — so with
                a database built, no config change is needed at all. Either
                config key or this flag forces one path over the other.

    --zip       Also zip this run's output file(s) into one archive
                (output/<translation>_<format>.zip) alongside the originals.

    --test      Quick-render mode: restrict to Genesis chapter 1 (known
                trouble spots from earlier testing) and Matthew chapters 1-5
                (5 opens the Sermon on the Mount, exercising the
                words-of-Christ red-letter feature; 1-4 included because
                e-Sword's own chapter picker won't let you navigate into a
                book at all if chapter 1 is missing), overriding
                config.yaml's "books"/"chapters" keys in memory (the file
                itself is untouched). For fast iteration on layout/CSS
                changes without a full-Bible build.

Examples:
    python main.py
    python main.py config_nt.yaml --format mysword
    python main.py --format all
    python main.py --composer table
    python main.py --format mysword --zip
    python main.py --format mysword --mode L2 --test
"""

import argparse
import zipfile
from pathlib import Path

import yaml

from translit import make_transliterator
from models import MappingDirection
from composer import AlignmentComposer
from table_composer import TableComposer, DrashComposer
from verse_formatter import (
    ESwordLemmaFormatter,
    ESwordLemmaDetailFormatter,
    ESwordReverseInterlinearFormatter,
    ESwordForwardInterlinearFormatter,
    MySwordLemmaFormatter,
    MySwordLemmaDetailFormatter,
    MySwordScriptFormatter,
    MySwordReverseInterlinearFormatter, ESwordScriptFormatter,
    MySwordForwardInterlinearFormatter,
    ESwordDrashLemmaFormatter,
    ESwordDrashLemmaDetailFormatter,
    ESwordDrashScriptFormatter,
    MySwordDrashLemmaFormatter,
    MySwordDrashLemmaDetailFormatter,
    MySwordDrashScriptFormatter,
)
from esword_writer import ESwordWriter
from mysword_writer import MySwordWriter
from osis_writer import OSISWriter


# ----------------------------------------------------------------- config

def load_config(path: str = "config.yaml", composer_override: str = None) -> dict:
    """Load and resolve pipeline configuration from YAML file."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    translation = cfg["translation"]
    cfg["table_db"] = Path(cfg.get("table_db", "data/bsb_tables.db"))

    composer = composer_override or cfg.get("composer")
    if not composer:
        # Auto-detect: prefer the precomputed database when it's actually
        # there — far cheaper than a live source/alignment/target join —
        # and only fall back to 'alignment' when no database has been built
        # yet. An explicit 'composer' key (or --composer) always wins over
        # this, so you can still force 'alignment' with a database present.
        composer = "table" if cfg["table_db"].exists() else "alignment"
    cfg["composer"] = composer

    if cfg["composer"] == "alignment":
        data_root = Path(cfg.get("data_root", "../"))
        for testament in ("ot", "nt"):
            src = cfg["sources"][testament]
            for key in ("source", "alignment", "target"):
                src[key] = data_root / src[key]
    # else: table_db is already resolved above, and the 'sources' block
    # (macula-hebrew/macula-greek/Alignments paths) is left untouched, so
    # nothing downstream ever reads or loads those text sources.

    cfg["annotations"] = Path(cfg.get("annotations", "data/bsb_annotations.json"))
    cfg["crossrefs"]   = Path(cfg.get("crossrefs", "data/bsb_xrefs.json"))
    cfg["output"]["dir"] = Path(cfg["output"]["dir"])

    cfg["abbrev"] = {
        "intralinear":         f"{translation}i",
        "intralinear_stacked": f"{translation}is",
        "interlinear":         f"{translation}ri+",
    }

    return cfg


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build an intralinear/interlinear Bible module."
    )
    parser.add_argument(
        "config", nargs="?", default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--format", dest="output_format",
        choices=["esword", "mysword", "osis", "all"], default="esword",
        help="Output format (default: esword); 'all' builds every target",
    )
    parser.add_argument(
        "--mode", dest="render_mode",
        choices=["intralinear", "interlinear", "reverse", "L1", "L2", "L3", "intra", "inter", "rev"],
        default="intralinear",
        help="Render mode (default: intralinear, which builds all three BTB "
             "tiers together); L1/L2/L3 build a single tier alone; ignored "
             "when --format=all",
    )
    parser.add_argument(
        "--composer", dest="composer", choices=["alignment", "table", "drash"], default=None,
        help="Data source (default: config.yaml's 'composer' key if set, "
             "otherwise auto-detected from whether table_db exists on disk); "
             "overrides both if given. 'drash' builds DTB instead of BTB -- "
             "only supported with --format esword/mysword and --mode "
             "intralinear/L1/L2/L3",
    )
    parser.add_argument(
        "--zip", action="store_true",
        help="Also zip this run's output file(s) into one archive in the output directory",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Quick-render mode: restrict to Genesis chapter 1 and Matthew "
             "chapters 1-5 (5 opens the Sermon on the Mount for red-letter "
             "testing; 1-4 included since e-Sword's chapter picker needs "
             "chapter 1 present to navigate into a book at all), overriding "
             "config.yaml's 'books'/'chapters' keys in memory (the file "
             "itself is untouched). For fast iteration on layout/CSS changes "
             "without a full-Bible build.",
    )
    args = parser.parse_args()

    # Normalize aliases
    if args.render_mode == 'intra':
        args.render_mode = 'intralinear'
    elif args.render_mode == 'inter':
        args.render_mode = 'interlinear'
    elif args.render_mode == 'rev':
        args.render_mode = 'reverse'

    return args


# ----------------------------------------------------------------- writer factory

def build_writers(output_format: str, render_mode: str,
                  transliterate, output_dir: Path, common_kw: dict,
                  drash: bool = False) -> list:
    """Return a list of configured writers for the requested format/mode.

    drash=True swaps the BTB-L1/L2/L3 formatter classes for their DTB
    (Drash Transliterated Bible) counterparts -- same rendering, different
    module identity (verse_formatter/intralinear.py). Only meaningful for
    output_format in ('esword', 'mysword') and render_mode in
    ('intralinear', 'L1', 'L2', 'L3'); main() validates that combination
    before calling this, since a DTB module's actual restored-names content
    comes from pairing it with DrashComposer, not from this flag alone.
    """
    l1_cls, l2_cls, l3_cls = (
        (ESwordDrashLemmaFormatter, ESwordDrashLemmaDetailFormatter, ESwordDrashScriptFormatter)
        if drash else
        (ESwordLemmaFormatter, ESwordLemmaDetailFormatter, ESwordScriptFormatter)
    )
    my_l1_cls, my_l2_cls, my_l3_cls = (
        (MySwordDrashLemmaFormatter, MySwordDrashLemmaDetailFormatter, MySwordDrashScriptFormatter)
        if drash else
        (MySwordLemmaFormatter, MySwordLemmaDetailFormatter, MySwordScriptFormatter)
    )

    def esword(profile_cls):
        return ESwordWriter(profile_cls(transliterate), **common_kw)

    def mysword(profile_cls, **extra):
        return MySwordWriter(profile_cls(transliterate), **common_kw, **extra)

    if output_format == 'all':
        return [
            esword(ESwordLemmaFormatter),
            esword(ESwordLemmaDetailFormatter),
            esword(ESwordScriptFormatter),
            esword(ESwordReverseInterlinearFormatter),
            mysword(MySwordLemmaFormatter),
            mysword(MySwordLemmaDetailFormatter),
            mysword(MySwordScriptFormatter),
            mysword(MySwordReverseInterlinearFormatter),
            OSISWriter(transliterate=transliterate),
        ]

    if output_format == 'esword':
        if render_mode == 'intralinear':
            return [esword(l1_cls), esword(l2_cls), esword(l3_cls)]
        elif render_mode == 'L1':
            return [esword(l1_cls)]
        elif render_mode == 'L2':
            return [esword(l2_cls)]
        elif render_mode == 'L3':
            return [esword(l3_cls)]
        elif render_mode == 'interlinear':
            profile_cls = ESwordForwardInterlinearFormatter
        else:  # reverse
            profile_cls = ESwordReverseInterlinearFormatter
        return [esword(profile_cls)]

    if output_format == 'mysword':
        if render_mode == 'intralinear':
            return [mysword(my_l1_cls), mysword(my_l2_cls), mysword(my_l3_cls)]
        elif render_mode == 'L1':
            return [mysword(my_l1_cls)]
        elif render_mode == 'L2':
            return [mysword(my_l2_cls)]
        elif render_mode == 'L3':
            return [mysword(my_l3_cls)]
        elif render_mode == 'interlinear':
            # rtl_ot: forward interlinear reorders Hebrew into its own
            # (right-to-left) word order, unlike intralinear/reverse
            # interlinear where English stays the primary, left-to-right
            # reading order regardless of source language.
            return [mysword(MySwordForwardInterlinearFormatter, rtl_ot=True)]
        else:  # reverse
            return [mysword(MySwordReverseInterlinearFormatter)]

    # osis
    return [OSISWriter(transliterate=transliterate)]


# ----------------------------------------------------------------- zip

def writer_abbreviation(writer) -> str:
    """A writer's module abbreviation (BSTB, BSRB, ...) -- MySword/e-Sword
    writers carry it on their profile (formatter instance); OSISWriter has
    no profile and carries it directly on itself."""
    return getattr(writer, 'profile', writer).abbreviation


def zip_outputs(paths: list, zip_path: Path) -> None:
    """Zip this run's output file(s) (flat, no directory structure) into one archive."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, arcname=path.name)
    print(f"Zipped {len(paths)} file(s) to {zip_path}")


# ----------------------------------------------------------------- main

def main():
    args   = parse_args()
    config = load_config(args.config, composer_override=args.composer)

    if args.test:
        config['books']    = ['Gen', 'Matt']
        config['chapters'] = {'Gen': 1, 'Matt': 5}
        print("--test: restricting to Genesis 1 and Matthew 1-5")

    if config['composer'] == 'drash' and (
        args.output_format not in ('esword', 'mysword')
        or args.render_mode not in ('intralinear', 'L1', 'L2', 'L3')
    ):
        raise SystemExit(
            "--composer drash only supports --format esword/mysword with "
            "--mode intralinear/L1/L2/L3 so far -- DTB has no interlinear/"
            "reverse/osis/all counterpart yet."
        )

    print(f"Config: {args.config}")
    print(f"Translation: {config['translation']} v{config['version']}")
    print(f"Format: {args.output_format}  Mode: {args.render_mode}  Composer: {config['composer']}")

    xlit_cfg     = config.get('transliteration', {})
    transliterate = make_transliterator(
        hebrew_scheme=xlit_cfg.get('hebrew', 'brill_simple'),
        greek_scheme=xlit_cfg.get('greek', 'SIMPLE'),
        syllable_sep=xlit_cfg.get('syllable_sep'),
        stress_marker=xlit_cfg.get('stress_marker'),
    )

    output_cfg = config['output']
    output_dir = output_cfg['dir']
    common_kw  = dict(
        headers    = output_cfg.get('headers', 1),
        notes      = output_cfg.get('notes', 1),
        xref       = output_cfg.get('xref', 0),
        red_letter = output_cfg.get('red_letter', 0),
        version    = config['version'],
    )

    writers = build_writers(
        args.output_format, args.render_mode,
        transliterate, output_dir, common_kw,
        drash=(config['composer'] == 'drash'),
    )

    for writer in writers:
        writer.open(output_dir)

    # render_mode is ignored for --format=all (fixed target-to-source set),
    # so the forward-interlinear join is only requested for an explicit,
    # single-format --mode interlinear run.
    direction = (MappingDirection.SOURCE_TO_TARGET
                 if args.render_mode == 'interlinear' and args.output_format != 'all'
                 else MappingDirection.TARGET_TO_SOURCE)

    if config["composer"] == "drash":
        composer = DrashComposer(config["table_db"], config=config, direction=direction)
    elif config["composer"] == "table":
        composer = TableComposer(config["table_db"], config=config, direction=direction)
    else:
        composer = AlignmentComposer(config, direction=direction)
    for osis_ref, tokens, header, xrefs in composer.iter_verses():
        for writer in writers:
            writer.add_verse(osis_ref, tokens, header=header, xrefs=xrefs)

    for writer in writers:
        writer.write()

    if args.zip:
        # Dedupe while preserving order -- a multi-format run (e.g. one
        # writer per platform for the same module) shouldn't repeat the
        # same abbreviation twice in the file name.
        seen = dict.fromkeys(writer_abbreviation(writer) for writer in writers)
        zip_path = output_dir / f"{'_'.join(seen)}.zip"
        zip_outputs([writer.output_path for writer in writers], zip_path)


if __name__ == '__main__':
    main()
