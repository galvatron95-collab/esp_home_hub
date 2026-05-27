"""Mock ESP32 client for the TTS WebSocket server.

Default behaviour: connect, send one prompt, receive audio until EOS, wrap
the PCM in a WAV header, send READY, exit. The READY frame is the
application-layer 'homeowner has taken the mail' acknowledgement that lets
the server return to accepting input.

Verification modes (mutually exclusive):

  --gate-test
      Send TWO prompts back-to-back without sending READY between them.
      The second prompt's audio should NOT arrive until --gate-release-after
      seconds, when this script finally sends READY. Demonstrates the
      server's gate works.

  --no-ready
      Send one prompt, receive audio, but DO NOT send READY. Exit. The
      server's log should show 'waiting for READY' and the connection
      stays open from the server's side until something else closes it.
      Useful for confirming the server actually waits.
"""

import argparse
import asyncio
import struct
import sys
import time
from pathlib import Path

import websockets

SERVER_URI = "ws://localhost:8765"
OUTPUT_PATH = Path(__file__).with_name("output.wav")
OUTPUT_PATH_2 = Path(__file__).with_name("output_2.wav")
SAMPLE_RATE = 16000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1
EXPECTED_MAX_CHUNK = 4096
READY_MARKER = "READY"


def wrap_wav(pcm: bytes) -> bytes:
    """Wrap raw 16 kHz / 16-bit / mono PCM in a minimal WAV header."""
    byte_rate = SAMPLE_RATE * NUM_CHANNELS * BITS_PER_SAMPLE // 8
    block_align = NUM_CHANNELS * BITS_PER_SAMPLE // 8
    data_size = len(pcm)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ", 16, 1, NUM_CHANNELS, SAMPLE_RATE,
        byte_rate, block_align, BITS_PER_SAMPLE,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + pcm
    riff = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt_chunk) + len(data_chunk), b"WAVE")
    return riff + fmt_chunk + data_chunk


async def drain_one_stream(ws, label: str) -> tuple[bytes, list[str]]:
    """Receive binary chunks until the 0-byte EOS frame. Returns the
    concatenated PCM and a list of validation problems."""
    chunks: list[bytes] = []
    eos_seen = False
    oversized = 0
    while True:
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=30.0)
        except asyncio.TimeoutError:
            return b"".join(chunks), [f"{label}: timed out waiting for audio"]
        if isinstance(frame, str):
            return b"".join(chunks), [f"{label}: unexpected text frame: {frame!r}"]
        if len(frame) == 0:
            eos_seen = True
            print(f"  {label}: EOS")
            break
        if len(frame) > EXPECTED_MAX_CHUNK:
            oversized += 1
        chunks.append(frame)
        print(f"  {label}: chunk {len(chunks):>4}: {len(frame)} bytes")

    pcm = b"".join(chunks)
    problems = []
    if not eos_seen:
        problems.append(f"{label}: no EOS marker")
    if not chunks:
        problems.append(f"{label}: no audio chunks received")
    if oversized:
        problems.append(f"{label}: {oversized} chunk(s) over {EXPECTED_MAX_CHUNK} bytes")
    if len(pcm) % (BITS_PER_SAMPLE // 8 * NUM_CHANNELS) != 0:
        problems.append(f"{label}: PCM length not aligned")
    return pcm, problems


async def run_default(text: str) -> int:
    """Send one prompt, drain, send READY, exit."""
    async with websockets.connect(SERVER_URI) as ws:
        print(f"connected to {SERVER_URI}")
        await ws.send(text)
        print(f"sent: {text!r}")
        pcm, problems = await drain_one_stream(ws, "stream1")
        if problems:
            for p in problems:
                print(f"FAIL: {p}")
            return 1
        OUTPUT_PATH.write_bytes(wrap_wav(pcm))
        print(f"wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
        await ws.send(READY_MARKER)
        print(f"sent READY ({len(pcm)} bytes received before ack)")
    print("OK: handshake completed cleanly")
    return 0


async def run_no_ready(text: str) -> int:
    """Send one prompt, drain, exit WITHOUT sending READY. The server's
    log should report 'waiting for READY'. This script returns 0 if it
    received audio + EOS cleanly; the actual gate-holding behaviour is
    visible only in the server's log."""
    async with websockets.connect(SERVER_URI) as ws:
        print(f"connected to {SERVER_URI}")
        await ws.send(text)
        print(f"sent: {text!r}")
        pcm, problems = await drain_one_stream(ws, "stream1")
        if problems:
            for p in problems:
                print(f"FAIL: {p}")
            return 1
        OUTPUT_PATH.write_bytes(wrap_wav(pcm))
        print(f"wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
        print("NOT sending READY. Closing connection. Check server log for "
              "'waiting for READY' confirming the gate held.")
    return 0


async def run_gate_test(text: str, gate_release_after: float) -> int:
    """Send TWO prompts back-to-back without READY between them. Time how
    long the second stream takes to arrive. If the server's gate works,
    the second stream's first chunk should arrive AFTER we send READY,
    not before."""
    async with websockets.connect(SERVER_URI) as ws:
        print(f"connected to {SERVER_URI}")
        await ws.send(text)
        print(f"sent first prompt: {text!r}")
        pcm1, problems1 = await drain_one_stream(ws, "stream1")
        if problems1:
            for p in problems1:
                print(f"FAIL: {p}")
            return 1
        t_eos1 = time.monotonic()
        OUTPUT_PATH.write_bytes(wrap_wav(pcm1))
        print(f"wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")

        second_text = f"Second message after gate. {text}"
        await ws.send(second_text)
        print(f"sent second prompt WITHOUT READY: {second_text!r}")

        recv_task = asyncio.create_task(drain_one_stream(ws, "stream2"))

        print(f"sleeping {gate_release_after}s before sending READY...")
        await asyncio.sleep(gate_release_after)
        t_ready_sent = time.monotonic()
        await ws.send(READY_MARKER)
        print(f"sent READY at +{t_ready_sent - t_eos1:.2f}s after stream1 EOS")

        pcm2, problems2 = await recv_task
        t_eos2 = time.monotonic()
        if problems2:
            for p in problems2:
                print(f"FAIL: {p}")
            return 1
        OUTPUT_PATH_2.write_bytes(wrap_wav(pcm2))
        print(f"wrote {OUTPUT_PATH_2} ({OUTPUT_PATH_2.stat().st_size} bytes)")

        await ws.send(READY_MARKER)
        print("sent final READY")

        gap = t_eos2 - t_ready_sent
        print(f"stream2 completed {gap:.2f}s after READY")
        if t_eos2 < t_ready_sent:
            print("FAIL: stream2 finished BEFORE READY was sent - gate did not hold")
            return 1
    print("OK: gate held second request until READY arrived")
    return 0


def main() -> int:
    global SERVER_URI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="*", default=["Hello from the test client."])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-ready", action="store_true",
                      help="Receive one stream and exit without sending READY.")
    mode.add_argument("--gate-test", action="store_true",
                      help="Send two prompts, verify the second is gated on READY.")
    parser.add_argument("--gate-release-after", type=float, default=3.0,
                        help="Seconds to wait before sending READY in --gate-test.")
    parser.add_argument("--server", default=SERVER_URI,
                        help=f"WebSocket server URI (default {SERVER_URI}).")
    args = parser.parse_args()

    SERVER_URI = args.server

    text = " ".join(args.text)
    if args.no_ready:
        return asyncio.run(run_no_ready(text))
    if args.gate_test:
        return asyncio.run(run_gate_test(text, args.gate_release_after))
    return asyncio.run(run_default(text))


if __name__ == "__main__":
    sys.exit(main())
