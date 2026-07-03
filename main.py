"""
main.py — Intralinear Bible module builder

Usage:
    python main.py [config.yaml] [--format FORMAT] [--mode MODE] [--composer SOURCE]

    --format    esword     e-Sword LT (.bbli)          [default]
                mysword    MySword (.bbl.mybible)
                osis       OSIS XML
                all        build every output target in one pass

    --mode      intralinear   English + source annotation above  [default]
                interlinear   reverse interlinear (source-primary columns)

    --composer  alignment  live join across macula-hebrew/macula-greek/
                           Alignments
                table      read table_db (built by utils/import_bsb_table.py)
                           instead
                Default: config.yaml's "composer" key if set, otherwise
                auto-detected from whether table_db exists on disk — so with
                a database built, no config change is needed at all. Either
                config key or this flag forces one path over the other.

Examples:
    python main.py
    python main.py config_nt.yaml --format mysword
    python main.py --format all
    python main.py --composer table
"""

import argparse
from pathlib import Path

import yaml

from translit import make_transliterator
from composer import AlignmentComposer
from table_composer import TableComposer
from verse_formatter import (
    ESwordIntralinearFormatter,
    ESwordReverseInterlinearFormatter,
    MySwordIntralinearFormatter,
    MySwordStackedFormatter,
    MySwordReverseInterlinearFormatter, ESwordStackedFormatter,
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
        choices=["intralinear", "interlinear", "stacked", "intra", "inter"],
        default="intralinear",
        help="Render mode (default: intralinear); ignored when --format=all",
    )
    parser.add_argument(
        "--composer", dest="composer", choices=["alignment", "table"], default=None,
        help="Data source (default: config.yaml's 'composer' key if set, "
             "otherwise auto-detected from whether table_db exists on disk); "
             "overrides both if given",
    )
    args = parser.parse_args()

    # Normalize aliases
    if args.render_mode == 'intra':
        args.render_mode = 'intralinear'
    elif args.render_mode == 'inter':
        args.render_mode = 'interlinear'

    return args


# ----------------------------------------------------------------- writer factory

def build_writers(output_format: str, render_mode: str,
                  transliterate, output_dir: Path, common_kw: dict) -> list:
    """Return a list of configured writers for the requested format/mode."""

    def esword(profile_cls):
        return ESwordWriter(profile_cls(transliterate), **common_kw)

    def mysword(profile_cls):
        return MySwordWriter(profile_cls(transliterate), **common_kw)

    if output_format == 'all':
        return [
            esword(ESwordIntralinearFormatter),
            esword(ESwordReverseInterlinearFormatter),
            mysword(MySwordIntralinearFormatter),
            mysword(MySwordStackedFormatter),
            mysword(MySwordReverseInterlinearFormatter),
            OSISWriter(transliterate=transliterate),
        ]

    if output_format == 'esword':
        if render_mode == 'intralinear':
            return [esword(ESwordIntralinearFormatter), esword(ESwordStackedFormatter)]
        elif render_mode == 'stacked':
            profile_cls = ESwordStackedFormatter
        else:
            profile_cls = ESwordReverseInterlinearFormatter
        return [esword(profile_cls)]

    if output_format == 'mysword':
        if render_mode == 'intralinear':
            return [mysword(MySwordIntralinearFormatter), mysword(MySwordStackedFormatter)]
        return [mysword(MySwordReverseInterlinearFormatter)]

    # osis
    return [OSISWriter(transliterate=transliterate)]


# ----------------------------------------------------------------- main

def main():
    args   = parse_args()
    config = load_config(args.config, composer_override=args.composer)

    print(f"Config: {args.config}")
    print(f"Translation: {config['translation']} v{config['version']}")
    print(f"Format: {args.output_format}  Mode: {args.render_mode}  Composer: {config['composer']}")

    xlit_cfg     = config.get('transliteration', {})
    transliterate = make_transliterator(
        hebrew_scheme=xlit_cfg.get('hebrew', 'brill_simple'),
        greek_scheme=xlit_cfg.get('greek', 'SIMPLE'),
    )

    output_cfg = config['output']
    output_dir = output_cfg['dir']
    common_kw  = dict(
        headers = output_cfg.get('headers', 1),
        notes   = output_cfg.get('notes', 1),
        xref    = output_cfg.get('xref', 0),
        version = config['version'],
    )

    writers = build_writers(
        args.output_format, args.render_mode,
        transliterate, output_dir, common_kw,
    )

    for writer in writers:
        writer.open(output_dir)

    if config["composer"] == "table":
        composer = TableComposer(config["table_db"], config=config)
    else:
        composer = AlignmentComposer(config)
    for osis_ref, tokens, header, xrefs in composer.iter_verses():
        for writer in writers:
            writer.add_verse(osis_ref, tokens, header=header, xrefs=xrefs)

    for writer in writers:
        writer.write()


if __name__ == '__main__':
    main()
