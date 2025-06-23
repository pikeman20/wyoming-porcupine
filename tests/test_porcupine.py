import asyncio
import sys
import wave
import socket
from asyncio.subprocess import PIPE
from pathlib import Path
import os

import pytest
from wyoming.audio import AudioStart, AudioStop, wav_to_chunks
from wyoming.event import async_read_event, async_write_event
from wyoming.info import Describe, Info
from wyoming.wake import Detect, Detection, NotDetected
from tees_stream_reader import TeeStreamReader

_DIR = Path(__file__).parent
_SAMPLES_PER_CHUNK = 1024
_DETECTION_TIMEOUT = 10
_TCP_PORT = 0  # 0 means the OS will assign a free port

#Commentout the WYOMING_TEST_PORT set when not testing with server side
#os.environ["WYOMING_TEST_PORT"] = "9899"
@pytest.mark.asyncio
async def test_porcupine() -> None:
    
    """Test a detection with sample audio using stdio on Linux and TCP on Windows."""
    is_windows = sys.platform.startswith("win")
    tcp_port = None
    external_server = False

    custom_port = os.environ.get("WYOMING_TEST_PORT")
    if is_windows and custom_port:
        try:
            tcp_port = int(custom_port)
            uri = f"tcp://127.0.0.1:{tcp_port}"
            external_server = True
            print(f"[INFO] Using external server on port {tcp_port}")
        except ValueError:
            print("[ERROR] Invalid WYOMING_TEST_PORT value. Falling back to default behavior.")
            tcp_port = None
            external_server = False

    if is_windows and not external_server:
        # Find a free TCP port dynamically
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        _, tcp_port = sock.getsockname()
        sock.close()
        uri = f"tcp://127.0.0.1:{tcp_port}"
    elif not is_windows:
        uri = "stdio://"

    proc = None
    if not external_server:
        # Start the wyoming_wakeword server with process group/session for cleanup
        creationflags = 0
        start_new_session = False
        if is_windows:
            # CREATE_NEW_PROCESS_GROUP = 0x00000200
            creationflags = 0x00000200
        else:
            # On Unix, start new session for easier cleanup
            start_new_session = True

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "wyoming_porcupine",
            "--access-key", "15VN64XFfJpwNU0sM9pcZOfu3bWsfDsb/22KJDhBPd0cc1JYxFA8qg==",
            "--uri",
            uri,
            stdout=PIPE,
            stderr=PIPE,
            stdin=PIPE if not is_windows else None,  # No stdin for TCP
            creationflags=creationflags if is_windows else 0,
            start_new_session=start_new_session if not is_windows else False,
        )

    # Wait for the server to be ready (retry loop for TCP)
    import time

    max_wait = 30  # seconds
    start_time = time.monotonic()
    reader = None
    writer = None
    connected = False
    last_err = None

    if is_windows:
        # Retry TCP connection until server is ready or timeout
        while time.monotonic() - start_time < max_wait:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
                connected = True
                break
            except (ConnectionRefusedError, OSError) as e:
                last_err = e
                await asyncio.sleep(0.2)
        if not connected:
            if not external_server:
                stderr_output = await proc.stderr.read()
            else:
                stderr_output = b"You're using External Server Mode. Please check with the external server if it is running."
            raise RuntimeError(f"Could not connect to server on TCP port {tcp_port}: {last_err}\n[STDERR]: {stderr_output.decode()}")
    else:
        # Stdio for Linux
        assert proc.stdout is not None
        assert proc.stdin is not None
        reader = proc.stdout
        writer = proc.stdin

    tee_reader = TeeStreamReader(reader)

    try:
        # Check info
        await async_write_event(Describe().event(), writer)
        while True:
            event = await asyncio.wait_for(
                async_read_event(tee_reader), timeout=_DETECTION_TIMEOUT
            )

            if event is None:
                print(tee_reader._buffer.decode(errors="replace"))
                stderr_output = await proc.stderr.read()
                print("[STDERR]", stderr_output.decode(errors="replace"))
                assert False, "No event received"

            if not Info.is_type(event.type):
                continue

            info = Info.from_event(event)
            assert len(info.wake) == 1, "Expected one wake service"
            wake = info.wake[0]
            assert len(wake.models) > 0, "Expected at least one model"

            model_found = False
            for ww_model in wake.models:
                if ww_model.name == "ok home":
                    assert ww_model.description == "ok home (en)"
                    model_found = True
                    break

            assert model_found, "Expected 'ok home' model"
            break

        # Use the 'ok home' model
        await async_write_event(Detect(names=["ok home"]).event(), writer)

        # Test positive WAV
        with wave.open(str(_DIR / "ok_home_gen.wav"), "rb") as ok_home_wav:
            await async_write_event(
                AudioStart(
                    rate=ok_home_wav.getframerate(),
                    width=ok_home_wav.getsampwidth(),
                    channels=ok_home_wav.getnchannels(),
                ).event(),
                writer,
            )
            for chunk in wav_to_chunks(ok_home_wav, _SAMPLES_PER_CHUNK):
                await async_write_event(chunk.event(), writer)

        await async_write_event(AudioStop().event(), writer)

        while True:
            event = await asyncio.wait_for(
                async_read_event(tee_reader), timeout=_DETECTION_TIMEOUT
            )
            if event is None:
                stderr = await proc.stderr.read()
                assert False, stderr.decode()

            if not Detection.is_type(event.type):
                continue

            detection = Detection.from_event(event)
            assert detection.name == "ok home"  # success
            break

        # Test negative WAV
        with wave.open(str(_DIR / "snowboy.wav"), "rb") as snowboy_wav:
            await async_write_event(
                AudioStart(
                    rate=snowboy_wav.getframerate(),
                    width=snowboy_wav.getsampwidth(),
                    channels=snowboy_wav.getnchannels(),
                ).event(),
                writer,
            )
            for chunk in wav_to_chunks(snowboy_wav, _SAMPLES_PER_CHUNK):
                await async_write_event(chunk.event(), writer)

        await async_write_event(AudioStop().event(), writer)

        while True:
            event = await asyncio.wait_for(async_read_event(tee_reader), timeout=1)
            if event is None:
                stderr = await proc.stderr.read()
                assert False, stderr.decode()

            if not NotDetected.is_type(event.type):
                continue

            # Should receive a not-detected message after audio-stop
            break

    finally:
        # Clean up
        try:
            if is_windows:
                # Close TCP connection
                if writer:
                    writer.close()
                    await writer.wait_closed()
            else:
                # Close stdin for stdio
                if proc and proc.stdin:
                    proc.stdin.close()
        except Exception:
            pass

        # Only terminate process if we started it
        if not external_server and proc:
            # Terminate the process group/session for robust cleanup
            try:
                if is_windows:
                    # Send CTRL_BREAK_EVENT to process group
                    import signal
                    if proc.pid:
                        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    # Send SIGTERM to the process group
                    if proc.pid:
                        if hasattr(os, "killpg"):
                            import signal as _signal
                            os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
            except Exception:
                # Fallback to terminate
                try:
                    proc.terminate()
                except Exception:
                    pass

            # Wait for process to exit
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    proc.kill()  # Force kill if it doesn't terminate gracefully
                except Exception:
                    pass
            _, stderr = await proc.communicate()

            # Accept normal exit or forced termination (SIGTERM/CTRL_BREAK)
            acceptable_codes = [0]
            if is_windows:
                # 0xC000013A == 3221225786: terminated by CTRL_BREAK_EVENT or CTRL_C_EVENT
                acceptable_codes.append(3221225786)
            else:
                # -15: SIGTERM
                acceptable_codes.append(-15)
            assert proc.returncode in acceptable_codes, f"Unexpected exit code {proc.returncode}: {stderr.decode()}"

            # Verify the TCP port is free (only for Windows)
            if is_windows:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind(("127.0.0.1", tcp_port))
                except socket.error:
                    assert False, f"Port {tcp_port} is still in use"
                finally:
                    sock.close()