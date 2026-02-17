import logging
import importlib
import time
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger("javis.ambient")


class AmbientSoundService:
    def __init__(
        self,
        model_id: str,
        confidence_threshold: float = 0.45,
        top_k: int = 2,
        min_emit_interval_seconds: float = 1.5,
        sample_rate: int = 16000,
    ):
        self.model_id = model_id
        self.confidence_threshold = confidence_threshold
        self.top_k = top_k
        self.min_emit_interval_seconds = min_emit_interval_seconds
        self.sample_rate = sample_rate

        self._feature_extractor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._load_failed = False
        self._last_emit_at: dict[tuple[str, str], float] = {}

    def _ensure_model(self) -> bool:
        if self._load_failed:
            return False
        if self._model is not None and self._feature_extractor is not None and self._torch is not None:
            return True

        try:
            torch = cast(Any, importlib.import_module("torch"))
            transformers = cast(Any, importlib.import_module("transformers"))
            auto_feature_extractor = cast(Any, getattr(transformers, "AutoFeatureExtractor"))
            auto_model = cast(Any, getattr(transformers, "AutoModelForAudioClassification"))

            self._torch = torch
            self._feature_extractor = auto_feature_extractor.from_pretrained(self.model_id)
            self._model = auto_model.from_pretrained(self.model_id)
            if getattr(torch, "cuda").is_available():
                self._model = self._model.to("cuda")
            self._model.eval()
            logger.info("ambient_model_loaded model=%s", self.model_id)
            return True
        except Exception as exc:
            self._load_failed = True
            logger.warning("ambient_model_unavailable model=%s error=%s", self.model_id, exc)
            return False

    def _pcm_to_float32(self, audio_bytes: bytes) -> NDArray[np.float32]:
        pcm = audio_bytes if len(audio_bytes) % 2 == 0 else audio_bytes[:-1]
        if not pcm:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    def _should_emit(self, session_id: str, label: str) -> bool:
        key = (session_id, label)
        now = time.time()
        last = self._last_emit_at.get(key)
        if last is not None and now - last < self.min_emit_interval_seconds:
            return False
        self._last_emit_at[key] = now
        return True

    def detect_events(
        self,
        audio_bytes: bytes,
        session_id: str,
        segment_id: str,
        started_at: float,
        ended_at: float,
    ) -> list[dict[str, str | float | None]]:
        if not self._ensure_model():
            return []

        waveform = self._pcm_to_float32(audio_bytes)
        if waveform.size < int(self.sample_rate * 0.25):
            return []

        assert self._feature_extractor is not None
        assert self._model is not None
        assert self._torch is not None

        try:
            feature_extractor = cast(Any, self._feature_extractor)
            model = cast(Any, self._model)
            torch = cast(Any, self._torch)

            inputs = feature_extractor(
                waveform,
                sampling_rate=self.sample_rate,
                return_tensors="pt",
            )
            if torch.cuda.is_available():
                inputs = {key: value.to("cuda") for key, value in inputs.items()}
            with torch.no_grad():
                logits = model(**inputs).logits
            probs = torch.softmax(logits[0], dim=-1)
            top_values, top_indices = torch.topk(probs, k=min(self.top_k, probs.shape[-1]))
        except Exception as exc:
            logger.warning("ambient_inference_failed session=%s segment=%s error=%s", session_id, segment_id, exc)
            return []

        events: list[dict[str, str | float | None]] = []
        model = cast(Any, self._model)
        id_to_label = getattr(model.config, "id2label", {})
        for value, index in zip(top_values.tolist(), top_indices.tolist(), strict=False):
            confidence = float(value)
            if confidence < self.confidence_threshold:
                continue

            label = str(id_to_label.get(int(index), f"label_{index}"))
            if not self._should_emit(session_id=session_id, label=label):
                continue

            events.append(
                {
                    "type": "ambient",
                    "session_id": session_id,
                    "segment_id": segment_id,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "text": label,
                    "confidence": confidence,
                }
            )
        return events
