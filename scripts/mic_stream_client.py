import argparse
import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
import sys
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.javis_stt.client_utils import build_ws_url, pcm16_bytes_per_second


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="ws://192.168.219.106:8765")
    parser.add_argument("--session-id", default="mac-client")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--chunk-ms", type=int, default=80)
    parser.add_argument("--wav", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--show-events", action="store_true")
    parser.add_argument("--debug-send", action="store_true")
    return parser.parse_args()


def build_logger(log_file: str | None):
    fp = None
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fp = path.open("a", encoding="utf-8")

    def _log(message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
        print(line)
        if fp is not None:
            fp.write(line + "\n")
            fp.flush()

    return _log, fp


def build_healthz_url(server_url: str) -> str:
    parsed = urllib.parse.urlparse(server_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urllib.parse.urlunparse((scheme, parsed.netloc, "/healthz", "", "", ""))


async def wait_for_server_health(server_url: str, log, timeout_seconds: float) -> None:
    healthz_url = build_healthz_url(server_url)
    while True:
        try:
            def _probe() -> bool:
                with urllib.request.urlopen(healthz_url, timeout=timeout_seconds) as response:
                    return response.status == 200

            ok = await asyncio.to_thread(_probe)
            if ok:
                return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass

        log(f"[health] waiting for server: {healthz_url}")
        await asyncio.sleep(1)


def list_input_devices() -> list[tuple[int, str]]:
    import importlib

    sd = importlib.import_module("sounddevice")
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) > 0:
            out.append((idx, str(dev.get("name", f"device-{idx}"))))
    return out


def resolve_input_device(device: str | None) -> int | None:
    if not device:
        return None
    if device.isdigit():
        return int(device)

    lowered = device.lower()
    for idx, name in list_input_devices():
        if lowered in name.lower():
            return idx
    raise ValueError(f"No input device matched: {device}")


async def recv_loop(ws, log, show_events: bool) -> None:
    while True:
        message = await ws.recv()
        if not show_events:
            continue
        data = json.loads(message)
        event_type = data.get("type", "unknown")
        text = data.get("text", "")
        log(f"[{event_type}] {text}")


async def send_wav(
    ws,
    wav_path: str,
    sample_rate: int,
    channels: int,
    chunk_ms: int,
    log,
    debug_send: bool,
) -> None:
    path = Path(wav_path)
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != sample_rate or wf.getnchannels() != channels or wf.getsampwidth() != 2:
            raise ValueError("WAV must be 16-bit PCM with matching sample rate/channels")

        frame_count = int((sample_rate * chunk_ms) / 1000)
        while True:
            frames = wf.readframes(frame_count)
            if not frames:
                break
            await ws.send(frames)
            if debug_send:
                log(f"[send] wav_bytes={len(frames)}")
            await asyncio.sleep(chunk_ms / 1000)


async def send_mic(
    ws,
    sample_rate: int,
    channels: int,
    chunk_ms: int,
    log,
    device: str | None,
    debug_send: bool,
) -> None:
    import importlib

    sd = importlib.import_module("sounddevice")
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=0)
    queue_hard_cap = 500
    dropped_chunks = 0
    chunk_bytes = int((pcm16_bytes_per_second(sample_rate, channels) * chunk_ms) / 1000)
    loop = asyncio.get_running_loop()

    def callback(indata, _frames, _time, _status):
        payload = bytes(indata)
        if len(payload) == 0:
            return
        loop.call_soon_threadsafe(push_chunk, payload)

    def push_chunk(payload: bytes) -> None:
        nonlocal dropped_chunks
        if queue.qsize() >= queue_hard_cap:
            dropped_chunks += 1
            if dropped_chunks <= 3 or dropped_chunks % 50 == 0:
                log(f"[send] queue overflow dropped_newest total={dropped_chunks}")
            return
        queue.put_nowait(payload)

    while True:
        try:
            device_index = resolve_input_device(device)
            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="int16",
                device=device_index,
                blocksize=max(1, int(sample_rate * chunk_ms / 1000)),
                callback=callback,
            ):
                if device_index is None:
                    log("[mic] using system default input")
                else:
                    log(f"[mic] using input device index={device_index}")
                while True:
                    payload = await queue.get()
                    await ws.send(payload)
                    if debug_send:
                        log(f"[send] mic_bytes={len(payload)}")
        except ConnectionClosed:
            raise
        except Exception as exc:
            log(f"[mic] stream error: {exc}")
            await asyncio.sleep(1)


async def run(args: argparse.Namespace) -> None:
    log, fp = build_logger(args.log_file)
    ws_url = build_ws_url(args.server, args.session_id)
    try:
        while True:
            await wait_for_server_health(args.server, log, timeout_seconds=2.0)
            log(f"[connect] {ws_url}")
            try:
                async with websockets.connect(ws_url, max_size=2**24, ping_interval=20, ping_timeout=20) as ws:
                    log("[connect] ok")

                    receiver = asyncio.create_task(recv_loop(ws, log, args.show_events))
                    if args.wav:
                        sender = asyncio.create_task(
                            send_wav(
                                ws,
                                args.wav,
                                args.sample_rate,
                                args.channels,
                                args.chunk_ms,
                                log,
                                args.debug_send,
                            )
                        )
                    else:
                        sender = asyncio.create_task(
                            send_mic(
                                ws,
                                args.sample_rate,
                                args.channels,
                                args.chunk_ms,
                                log,
                                args.device,
                                args.debug_send,
                            )
                        )

                    done, pending = await asyncio.wait(
                        {receiver, sender},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        if task.cancelled():
                            continue
                        exc = task.exception()
                        if exc is not None:
                            raise exc

                    if args.wav:
                        return
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                log(f"[connect] lost: {exc}. reconnecting in 2s")
                await asyncio.sleep(2)
            except Exception as exc:
                log(f"[connect] error: {exc}. reconnecting in 2s")
                await asyncio.sleep(2)
    finally:
        log("[connect] closed")
        if fp is not None:
            fp.close()


def main() -> None:
    args = parse_args()
    if args.list_devices:
        for idx, name in list_input_devices():
            print(f"{idx}: {name}")
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
