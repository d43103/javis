"""Tests for audio_devices module."""
import sys
import types

import numpy as np
import pytest

# stub sounddevice before importing — reuse existing stub if present
_FAKE_DEVICES = [
    {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 48000.0},
    {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "External USB Mic", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 44100.0},
    {"name": "AirPods Pro", "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 48000.0},
]

if "sounddevice" in sys.modules:
    sd_stub = sys.modules["sounddevice"]
else:
    sd_stub = types.ModuleType("sounddevice")
    sys.modules["sounddevice"] = sd_stub

sd_stub.query_devices = lambda: _FAKE_DEVICES

from src.audio_devices import (
    apply_gain_float32,
    apply_gain_int16,
    list_input_devices,
    list_output_devices,
)


def test_list_input_devices():
    devices = list_input_devices()
    assert len(devices) == 3  # MacBook Mic, External USB, AirPods
    names = [d["name"] for d in devices]
    assert "MacBook Pro Microphone" in names
    assert "MacBook Pro Speakers" not in names


def test_list_output_devices():
    devices = list_output_devices()
    assert len(devices) == 2  # Speakers, AirPods
    names = [d["name"] for d in devices]
    assert "MacBook Pro Speakers" in names
    assert "External USB Mic" not in names


def test_apply_gain_int16_unity():
    data = np.array([100, -100, 0, 32767], dtype=np.int16).tobytes()
    result = apply_gain_int16(data, 1.0)
    assert result == data


def test_apply_gain_int16_amplify():
    data = np.array([10000, -10000], dtype=np.int16).tobytes()
    result = apply_gain_int16(data, 2.0)
    samples = np.frombuffer(result, dtype=np.int16)
    assert samples[0] == 20000
    assert samples[1] == -20000


def test_apply_gain_int16_clips():
    data = np.array([30000, -30000], dtype=np.int16).tobytes()
    result = apply_gain_int16(data, 2.0)
    samples = np.frombuffer(result, dtype=np.int16)
    assert samples[0] == 32767
    assert samples[1] == -32768


def test_apply_gain_float32_unity():
    data = np.array([0.5, -0.5, 0.0], dtype=np.float32)
    result = apply_gain_float32(data, 1.0)
    assert result is data  # no copy when gain is 1.0


def test_apply_gain_float32_amplify():
    data = np.array([0.3, -0.3], dtype=np.float32)
    result = apply_gain_float32(data, 2.0)
    np.testing.assert_allclose(result, [0.6, -0.6], atol=1e-6)


def test_apply_gain_float32_clips():
    data = np.array([0.8, -0.8], dtype=np.float32)
    result = apply_gain_float32(data, 2.0)
    np.testing.assert_allclose(result, [1.0, -1.0], atol=1e-6)
