#!/usr/bin/env python3
import argparse
import asyncio
import logging
import platform
from functools import partial
from pathlib import Path
from typing import Dict
import json
from .handler import Keyword, PorcupineEventHandler, State

from wyoming.info import Attribution, Info, WakeModel, WakeProgram
from wyoming.server import AsyncServer

_LOGGER = logging.getLogger()
_DIR = Path(__file__).parent



async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="stdio://", help="unix:// or tcp://")
    parser.add_argument(
        "--data-dir", default=_DIR / "data", help="Path to directory lib/resources"
    )
    parser.add_argument("--system", help="linux or raspberry-pi")
    parser.add_argument("--sensitivity", type=float, default=0.5)
    parser.add_argument("--access-key", type=str, required=True)
    #
    parser.add_argument(
        "--custom-keyword-dir",
        action="append",
        default=[_DIR / "data/custom_models"],
        help="Path to directory with custom keywords",
    )
    #
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    if not args.system:
        machine = platform.machine().lower()
        if ("arm" in machine) or ("aarch" in machine):
            args.system = "raspberry-pi"
        else:
            args.system = "linux"

    args.data_dir = Path(args.data_dir)
    args.custom_keyword_dir = [Path(d) for d in args.custom_keyword_dir]

    # lang -> path
    pv_lib_paths: Dict[str, Path] = {}
    for lib_path in (args.data_dir / "lib" / "common").glob("*.pv"):
        lib_lang = lib_path.stem.split("_")[-1]
        pv_lib_paths[lib_lang] = lib_path

    # name -> keyword
    keywords: Dict[str, Keyword] = {}
    for kw_path in (args.data_dir / "resources").rglob("*.ppn"):
        kw_system = kw_path.stem.split("_")[-1]
        if kw_system != args.system:
            continue

        kw_lang = kw_path.parent.parent.name
        kw_name = kw_path.stem.rsplit("_", maxsplit=1)[0]
        keywords[kw_name] = Keyword(language=kw_lang, name=kw_name, model_path=kw_path)

    # custom models, files are of the form mykeyword_en_linux_v2_2_0.ppn
    for dir in args.custom_keyword_dir:
        for kw_path in dir.glob("*.ppn"):
            try:
                (kw_name, kw_lang, kw_system, _) = kw_path.stem.split("_", maxsplit=3)
            except:
                _LOGGER.warning("Incorrect keyword filename (%s), ignoring", kw_path)
                continue

            if kw_system != args.system:
                _LOGGER.warning("Incorrect keyword system (%s), ignoring", kw_path)
                continue

            keywords[kw_name] = Keyword(
                language=kw_lang, name=kw_name, model_path=kw_path
            )
            
    print(f"Found {len(keywords)} keywords: {json.dumps(keywords, indent=2, default=str)}")
    wyoming_info = Info(
        wake=[
            WakeProgram(
                name="porcupine",
                description="On-device wake word detection powered by deep learning ",
                attribution=Attribution(
                    name="Picovoice", url="https://github.com/Picovoice/porcupine"
                ),
                installed=True,
                models=[
                    WakeModel(
                        name=kw.name,
                        description=f"{kw.name} ({kw.language})",
                        attribution=Attribution(
                            name="Picovoice",
                            url="https://github.com/Picovoice/porcupine",
                        ),
                        installed=True,
                        languages=[kw.language],
                    )
                    for kw in keywords.values()
                ],
            )
        ],
    )

    state = State(pv_lib_paths=pv_lib_paths, keywords=keywords)

    _LOGGER.info("Ready")
    print(
        f"Wyoming Porcupine server started with {args.uri}"
    )
    # Start server
    server = AsyncServer.from_uri(args.uri)

    try:
        await server.run(partial(PorcupineEventHandler, wyoming_info, args, state))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
