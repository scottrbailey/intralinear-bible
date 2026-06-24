"""
main.py

Entry point: parse args, build writers, run composer, write output.
"""

import argparse
from pathlib import Path

import yaml

from translit import make_transliterator
from composer import BibleComposer
from verse_formatter import (
    ESwordIntralinearFormatter,
    ESwordReverseInterlinearFormatter,
    MySwordIntralinearFormatter,
    MySwordStackedFormatter,
    MySwordReverseInterlinearFormatter,
)
from esword_writer import ESwordWriter
from mysword_writer import MySwordWriter
from osis_writer import OSISWriter


# ----------------------------------------------------------------- config

def load_config(path: str = "config.yaml") -> dict:
    """Load and resolve pipeline configuration from YAML file."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    data_root   = Path(cfg.get("data_root", "../"))
    translation = cfg["translation"]

    for testament in ("ot", "nt"):
        src = cfg["sources"][testament]
        for key in ("source", "alignment", "target"):
            src[key] = data_root / src[key]

    cfg["annotations"] = Path(cfg.get("annotations", "data/bsb_annotations.json"))
    cfg["tsk"]         = Path(cfg.get("tsk", "data/tsk_xrefs.json"))
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
        choices=["intralinear", "interlinear", "intra", "inter"],
        default="intralinear",
        help="Render mode (default: intralinear); ignored when --format=all",
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
        profile_cls = (ESwordIntralinearFormatter if render_mode == 'intralinear'
                       else ESwordReverseInterlinearFormatter)
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
    config = load_config(args.config)

    print(f"Config: {args.config}")
    print(f"Translation: {config['translation']} v{config['version']}")
    print(f"Format: {args.output_format}  Mode: {args.render_mode}")

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

    composer = BibleComposer(config)
    for osis_ref, tokens, header, xrefs in composer.iter_verses():
        for writer in writers:
            writer.add_verse(osis_ref, tokens, header=header, xrefs=xrefs)

    for writer in writers:
        writer.write()


if __name__ == '__main__':
    main()
