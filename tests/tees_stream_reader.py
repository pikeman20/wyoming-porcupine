import asyncio
from typing import Optional


class TeeStreamReader:
    def __init__(self, reader: asyncio.StreamReader, debug: bool = True):
        self._reader = reader
        self._buffer = bytearray()
        self._debug = debug

    # Forwarded methods with tee behavior
    async def readline(self) -> bytes:
        line = await self._reader.readline()
        self._buffer.extend(line)
        if self._debug:
            print(f"[DEBUG] readline: {line.decode(errors='replace').strip()}")
        return line

    async def readexactly(self, n: int) -> bytes:
        data = await self._reader.readexactly(n)
        self._buffer.extend(data)
        if self._debug:
            preview = data[:50]
            suffix = b"..." if len(data) > 50 else b""
            print(f"[DEBUG] readexactly({n}): {preview}{suffix}")
        return data

    async def read(self, n: int = -1) -> bytes:
        data = await self._reader.read(n)
        self._buffer.extend(data)
        if self._debug:
            preview = data[:50]
            suffix = b"..." if len(data) > 50 else b""
            print(f"[DEBUG] read({n}): {preview}{suffix}")
        return data

    async def readuntil(self, separator: bytes = b'\n') -> bytes:
        data = await self._reader.readuntil(separator)
        self._buffer.extend(data)
        if self._debug:
            print(f"[DEBUG] readuntil({separator}): {data.decode(errors='replace').strip()}")
        return data

    def at_eof(self) -> bool:
        return self._reader.at_eof()

    def exception(self) -> Optional[BaseException]:
        return self._reader.exception()

    def get_debug_buffer(self) -> bytes:
        return bytes(self._buffer)

    # Optionally: expose the underlying transport for advanced use
    def get_transport(self):
        return self._reader._transport
