"""Audio device enumeration and gain utilities."""
import numpy as np


def list_input_devices() -> list[dict]:
    """Return list of input (microphone) devices via sounddevice."""
    import sounddevice as sd

    devices = sd.query_devices()
    return [
        {"index": i, "name": d["name"], "channels": d["max_input_channels"],
         "sample_rate": d["default_samplerate"]}
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def list_output_devices() -> list[dict]:
    """Return list of output (speaker) devices via sounddevice."""
    import sounddevice as sd

    devices = sd.query_devices()
    return [
        {"index": i, "name": d["name"], "channels": d["max_output_channels"],
         "sample_rate": d["default_samplerate"]}
        for i, d in enumerate(devices)
        if d["max_output_channels"] > 0
    ]


def apply_gain_int16(data: bytes, gain: float) -> bytes:
    """Apply gain to int16 PCM data, clipping to int16 range."""
    if gain == 1.0:
        return data
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    samples *= gain
    np.clip(samples, -32768, 32767, out=samples)
    return samples.astype(np.int16).tobytes()


def apply_gain_float32(data: np.ndarray, gain: float) -> np.ndarray:
    """Apply gain to float32 audio array, clipping to [-1.0, 1.0]."""
    if gain == 1.0:
        return data
    result = data * gain
    np.clip(result, -1.0, 1.0, out=result)
    return result
