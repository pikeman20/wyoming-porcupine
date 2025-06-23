#!/usr/bin/env python3
import argparse
import asyncio
import logging
import struct
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pvporcupine
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.wake import Detect, Detection, NotDetected


_LOGGER = logging.getLogger()
_DIR = Path(__file__).parent

DEFAULT_KEYWORD = "porcupine"


@dataclass
class Keyword:
    """Single porcupine keyword"""

    language: str
    name: str
    model_path: Path


@dataclass
class Detector:
    porcupine: pvporcupine.Porcupine
    sensitivity: float


class State:
    """State of system"""

    def __init__(self, pv_lib_paths: Dict[str, Path], keywords: Dict[str, Keyword]):
        self.pv_lib_paths = pv_lib_paths
        self.keywords = keywords

        # keyword name -> [detector]
        self.detector_cache: Dict[str, List[Detector]] = defaultdict(list)
        self.detector_lock = asyncio.Lock()

    async def get_porcupine(
        self, keyword_name: str, sensitivity: float, access_key: str
    ) -> Detector:
        keyword = self.keywords.get(keyword_name)
        if keyword is None:
            raise ValueError(f"No keyword {keyword_name}")

        # Check cache first for matching detector
        async with self.detector_lock:
            detectors = self.detector_cache.get(keyword_name)
            if detectors:
                detector = next(
                    (d for d in detectors if d.sensitivity == sensitivity), None
                )
                if detector is not None:
                    # Remove from cache for use
                    detectors.remove(detector)

                    _LOGGER.debug(
                        "Using detector for %s from cache (%s)",
                        keyword_name,
                        len(detectors),
                    )
                    return detector

        _LOGGER.debug("Loading %s for %s", keyword.name, keyword.language)
        porcupine = pvporcupine.create(
            model_path=str(self.pv_lib_paths[keyword.language]),
            keyword_paths=[str(keyword.model_path)],
            sensitivities=[sensitivity],
            access_key=access_key,
        )

        return Detector(porcupine, sensitivity)


# -----------------------------------------------------------------------------


class PorcupineEventHandler(AsyncEventHandler):
    """Event handler for clients."""

    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        state: State,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.client_id = str(time.monotonic_ns())
        self.state = state
        self.converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self.audio_buffer = bytes()
        self.detected = False

        self.detector: Optional[Detector] = None
        self.keyword_name: str = ""
        self.chunk_format: str = ""
        self.bytes_per_chunk: int = 0

        _LOGGER.debug("Client connected: %s", self.client_id)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info to client: %s", self.client_id)
            return True

        if Detect.is_type(event.type):
            detect = Detect.from_event(event)
            if detect.names:
                # TODO: use all names
                await self._load_keyword(detect.names[0])
        elif AudioStart.is_type(event.type):
            self.detected = False
        elif AudioChunk.is_type(event.type):
            if self.detector is None:
                # Default keyword
                await self._load_keyword(DEFAULT_KEYWORD)

            assert self.detector is not None

            chunk = AudioChunk.from_event(event)
            chunk = self.converter.convert(chunk)
            self.audio_buffer += chunk.audio

            while len(self.audio_buffer) >= self.bytes_per_chunk:
                unpacked_chunk = struct.unpack_from(
                    self.chunk_format, self.audio_buffer[: self.bytes_per_chunk]
                )
                keyword_index = self.detector.porcupine.process(unpacked_chunk)
                if keyword_index >= 0:
                    _LOGGER.debug(
                        "Detected %s from client %s", self.keyword_name, self.client_id
                    )
                    await self.write_event(
                        Detection(
                            name=self.keyword_name, timestamp=chunk.timestamp
                        ).event()
                    )

                self.audio_buffer = self.audio_buffer[self.bytes_per_chunk :]

        elif AudioStop.is_type(event.type):
            # Inform client if not detections occurred
            if not self.detected:
                # No wake word detections
                await self.write_event(NotDetected().event())

                _LOGGER.debug(
                    "Audio stopped without detection from client: %s", self.client_id
                )

            return False
        else:
            _LOGGER.debug("Unexpected event: type=%s, data=%s", event.type, event.data)

        return True

    async def disconnect(self) -> None:
        _LOGGER.debug("Client disconnected: %s", self.client_id)

        if self.detector is not None:
            # Return detector to cache
            async with self.state.detector_lock:
                self.state.detector_cache[self.keyword_name].append(self.detector)
                self.detector = None
                _LOGGER.debug(
                    "Detector for %s returned to cache (%s)",
                    self.keyword_name,
                    len(self.state.detector_cache[self.keyword_name]),
                )

    async def _load_keyword(self, keyword_name: str):
        self.detector = await self.state.get_porcupine(
            keyword_name, self.cli_args.sensitivity, self.cli_args.access_key
        )
        self.keyword_name = keyword_name
        self.chunk_format = "h" * self.detector.porcupine.frame_length
        self.bytes_per_chunk = self.detector.porcupine.frame_length * 2


# -----------------------------------------------------------------------------
