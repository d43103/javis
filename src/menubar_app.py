"""macOS menu bar app for Javis — connects to Mac Hub WebSocket server."""
import asyncio
import json
import logging
import queue
import threading

import rumps

from src.audio_devices import apply_gain_int16, apply_gain_float32, list_input_devices, list_output_devices

logger = logging.getLogger("javis.menubar")

# Gain range
GAIN_MIN = 0.0
GAIN_MAX = 2.0
GAIN_STEP = 0.1


def _get_input_devices():
    try:
        return list_input_devices()
    except Exception:
        return []


def _get_output_devices():
    try:
        return list_output_devices()
    except Exception:
        return []


class JavisMenuBarApp(rumps.App):
    def __init__(self, hub_url: str, session_id: str, auto_start: bool = False):
        super().__init__("J", quit_button=None)

        self._hub_url = hub_url
        self._session_id = session_id
        self._auto_start = auto_start

        self._running = False
        self._bridge_thread: threading.Thread | None = None
        self._bridge_loop: asyncio.AbstractEventLoop | None = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._mic_muted = False

        self._input_gain = 1.0
        self._output_gain = 1.0
        self._selected_input_device: str | None = None
        self._selected_output_device: int | None = None

        self._last_stt = ""
        self._last_ai = ""
        self._status = "idle"

        # Build menu items
        self._status_item = rumps.MenuItem("Status: idle")
        self._status_item.set_callback(None)

        # Build device submenus with initial device lists
        self._input_device_menu = rumps.MenuItem("Input Device")
        for dev in _get_input_devices():
            item = rumps.MenuItem(dev["name"], callback=self._make_input_device_callback(dev))
            self._input_device_menu.add(item)

        self._input_gain_label = rumps.MenuItem("Input Gain: 1.0x")
        self._input_gain_label.set_callback(None)
        self._input_gain_up = rumps.MenuItem("Input +", callback=self._on_input_gain_up)
        self._input_gain_down = rumps.MenuItem("Input -", callback=self._on_input_gain_down)

        self._output_device_menu = rumps.MenuItem("Output Device")
        for dev in _get_output_devices():
            item = rumps.MenuItem(dev["name"], callback=self._make_output_device_callback(dev))
            self._output_device_menu.add(item)

        self._output_gain_label = rumps.MenuItem("Output Gain: 1.0x")
        self._output_gain_label.set_callback(None)
        self._output_gain_up = rumps.MenuItem("Output +", callback=self._on_output_gain_up)
        self._output_gain_down = rumps.MenuItem("Output -", callback=self._on_output_gain_down)

        self._stt_item = rumps.MenuItem('STT: ""')
        self._stt_item.set_callback(None)
        self._ai_item = rumps.MenuItem('AI: ""')
        self._ai_item.set_callback(None)

        self._toggle_item = rumps.MenuItem("Start", callback=self._on_toggle)
        self._refresh_item = rumps.MenuItem("Refresh Devices", callback=self._on_refresh_devices)
        self._quit_item = rumps.MenuItem("Quit", callback=self._on_quit)

        self.menu = [
            self._status_item,
            None,  # separator
            self._input_device_menu,
            self._input_gain_label,
            self._input_gain_up,
            self._input_gain_down,
            None,
            self._output_device_menu,
            self._output_gain_label,
            self._output_gain_up,
            self._output_gain_down,
            None,
            self._stt_item,
            self._ai_item,
            None,
            self._toggle_item,
            self._refresh_item,
            self._quit_item,
        ]

        # Poll UI queue every 0.1s
        self._timer = rumps.Timer(self._poll_ui_queue, 0.1)
        self._timer.start()

        if self._auto_start:
            rumps.Timer(self._deferred_start, 0.5).start()

    def _deferred_start(self, timer):
        timer.stop()
        self._start_bridge()

    def _refresh_device_lists(self):
        """Rebuild device submenus. Only call after app run loop is active."""
        self._input_device_menu.clear()
        for dev in _get_input_devices():
            item = rumps.MenuItem(dev["name"], callback=self._make_input_device_callback(dev))
            if self._selected_input_device == str(dev["index"]):
                item.state = 1
            self._input_device_menu.add(item)

        self._output_device_menu.clear()
        for dev in _get_output_devices():
            item = rumps.MenuItem(dev["name"], callback=self._make_output_device_callback(dev))
            if self._selected_output_device == dev["index"]:
                item.state = 1
            self._output_device_menu.add(item)

    def _make_input_device_callback(self, dev):
        def cb(_):
            self._selected_input_device = str(dev["index"])
            self._refresh_device_lists()
            if self._running:
                self._restart_bridge()
        return cb

    def _make_output_device_callback(self, dev):
        def cb(_):
            self._selected_output_device = dev["index"]
            self._refresh_device_lists()
            if self._running:
                self._restart_bridge()
        return cb

    def _on_input_gain_up(self, _):
        self._input_gain = min(GAIN_MAX, round(self._input_gain + GAIN_STEP, 1))
        self._input_gain_label.title = f"Input Gain: {self._input_gain:.1f}x"

    def _on_input_gain_down(self, _):
        self._input_gain = max(GAIN_MIN, round(self._input_gain - GAIN_STEP, 1))
        self._input_gain_label.title = f"Input Gain: {self._input_gain:.1f}x"

    def _on_output_gain_up(self, _):
        self._output_gain = min(GAIN_MAX, round(self._output_gain + GAIN_STEP, 1))
        self._output_gain_label.title = f"Output Gain: {self._output_gain:.1f}x"

    def _on_output_gain_down(self, _):
        self._output_gain = max(GAIN_MIN, round(self._output_gain - GAIN_STEP, 1))
        self._output_gain_label.title = f"Output Gain: {self._output_gain:.1f}x"

    def _on_toggle(self, _):
        if self._running:
            self._stop_bridge()
        else:
            self._start_bridge()

    def _on_refresh_devices(self, _):
        self._refresh_device_lists()

    def _on_quit(self, _):
        self._stop_bridge()
        rumps.quit_application()

    # --- Bridge lifecycle ---

    def _start_bridge(self):
        if self._running:
            return
        self._running = True
        self._toggle_item.title = "Stop"

        def run_in_thread():
            loop = asyncio.new_event_loop()
            self._bridge_loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._run_hub_client())
            finally:
                loop.close()
                self._bridge_loop = None
                self._running = False

        self._bridge_thread = threading.Thread(target=run_in_thread, daemon=True)
        self._bridge_thread.start()

    async def _run_hub_client(self):
        import websockets
        import sounddevice as sd
        import numpy as np

        url = f"{self._hub_url}/ws/voice?session_id={self._session_id}"
        MIC_RATE = 16000
        TTS_RATE = 24000
        CHUNK_MS = 80
        chunk_frames = int(MIC_RATE * CHUNK_MS / 1000)

        async with websockets.connect(url, max_size=2**24, ping_interval=20) as ws:
            self._ui_queue.put(("status", "connected"))

            audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
            loop = asyncio.get_running_loop()

            def mic_callback(indata, _frames, _time, _status):
                loop.call_soon_threadsafe(audio_q.put_nowait, bytes(indata))

            async def sender():
                silence = bytes(chunk_frames * 2)
                with sd.RawInputStream(samplerate=MIC_RATE, channels=1,
                                       dtype="int16", blocksize=chunk_frames,
                                       callback=mic_callback):
                    while self._running:
                        try:
                            pcm = await asyncio.wait_for(audio_q.get(), 0.5)
                        except asyncio.TimeoutError:
                            continue
                        if self._mic_muted:
                            await ws.send(silence)
                        else:
                            pcm = apply_gain_int16(pcm, self._input_gain)
                            await ws.send(pcm)

            async def receiver():
                with sd.OutputStream(samplerate=TTS_RATE, channels=1,
                                      dtype="float32",
                                      device=self._selected_output_device) as stream:
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            audio = np.frombuffer(msg, dtype=np.float32)
                            audio = apply_gain_float32(audio, self._output_gain)
                            stream.write(audio.reshape(-1, 1))
                        elif isinstance(msg, str):
                            try:
                                evt = json.loads(msg)
                            except Exception:
                                continue
                            t = evt.get("type", "")
                            if t == "status":
                                v = evt.get("value", "")
                                self._ui_queue.put(("status", v))
                                self._mic_muted = (v == "speaking")
                            elif t == "partial":
                                self._ui_queue.put(("partial", evt.get("text", "")))
                            elif t == "final":
                                self._ui_queue.put(("final", evt.get("text", "")))
                            elif t == "ai":
                                self._ui_queue.put(("ai", evt.get("text", "")))

            s = asyncio.create_task(sender())
            r = asyncio.create_task(receiver())
            done, pending = await asyncio.wait({s, r}, return_when=asyncio.FIRST_EXCEPTION)
            for t in pending:
                t.cancel()

    def _stop_bridge(self):
        self._running = False
        if self._bridge_loop and self._bridge_loop.is_running():
            self._bridge_loop.call_soon_threadsafe(self._bridge_loop.stop)

        if self._bridge_thread:
            self._bridge_thread.join(timeout=5)
            self._bridge_thread = None

        self._bridge_loop = None
        self._mic_muted = False
        self._toggle_item.title = "Start"
        self._status_item.title = "Status: stopped"

    def _restart_bridge(self):
        self._stop_bridge()
        self._start_bridge()

    # --- UI polling ---

    def _poll_ui_queue(self, _):
        while True:
            try:
                kind, value = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self._status = value
                self._status_item.title = f"Status: {value}"
            elif kind == "partial":
                self._stt_item.title = f"STT: {value[:60]}"
            elif kind == "final":
                self._last_stt = value
                self._stt_item.title = f"STT: {value[:60]}"
            elif kind == "ai":
                self._last_ai = value
                self._ai_item.title = f"AI: {value[:60]}"
