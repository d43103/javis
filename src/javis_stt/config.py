from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class STTConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider: str = "faster_whisper"
    model_size: str = "large-v3"
    compute_type: str = "float16"
    language: str = "ko"
    beam_size: int = 5
    remote_base_url: str = "http://127.0.0.1:8011"
    remote_model: str = "Qwen/Qwen3-ASR-1.7B"
    remote_timeout_seconds: float = 60.0
    remote_realtime_path: str = "/v1/realtime"
    remote_realtime_chunk_bytes: int = 4096
    condition_on_previous_text: bool = True
    vad_filter: bool = True
    whisper_vad_filter: bool = False
    temperature: float = 0.0
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4
    min_segment_duration_seconds: float = 0.9
    segment_min_avg_logprob: float = -0.9
    segment_max_no_speech_prob: float = 0.75
    repeated_text_window_seconds: float = 4.0
    repeated_text_logprob_threshold: float = -0.25
    pre_roll_ms: int = 400
    hallucination_max_confidence: float = -0.15
    hallucination_exact_phrases: list[str] = Field(
        default_factory=lambda: [
            "시청해주셔서",
            "시청해 주셔서",
            "시청해주셔서 감사합니다.",
            "시청해 주셔서 감사합니다.",
            "구독과 좋아요 부탁드립니다.",
            "구독과 좋아요 부탁드려요.",
            "좋아요와 구독 부탁드립니다.",
            "좋아요 구독 부탁드립니다.",
            "좋아요와 구독 그리고 알림설정 부탁드립니다.",
            "한글자막 by 한효정",
            "한글 자막 by 한효정",
            "자막 제공 배달의민족",
            "아멘.",
            "아멘",
            "아멘, 다음 영상에서 만나요.",
            "아멘 다음 영상에서 만나요.",
            "다음 영상에서 만나요.",
        ]
    )
    hallucination_always_block_contains: list[str] = Field(
        default_factory=lambda: [
            "한글자막 by",
            "한글 자막 by",
            "자막 제공",
            "subtitle by",
            "subtitles by",
            "아멘",
            "다음 영상에서 만나요",
        ]
    )


class VADConfig(BaseModel):
    min_silence_duration_ms: int = 400
    speech_pad_ms: int = 300


class AIConfig(BaseModel):
    enabled: bool = True
    api_format: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:14b"
    timeout_seconds: int = 20
    max_retries: int = 2
    keep_alive: str = "0s"
    context_turn_limit: int = 10
    idle_flush_seconds: float = 2.1
    idle_flush_requires_sentence_end: bool = True
    max_utterance_hold_seconds: float = 6.0
    system_prompt: str = ""
    enable_thinking: bool = False


class TTSConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    enabled: bool = False
    provider: str = "vllm"
    base_url: str = "http://127.0.0.1:8031"
    model: str = "Qwen/Qwen3-TTS-0.6B"
    voice: str = "Sohee"
    sample_rate: int = 24000
    timeout_seconds: float = 30.0
    streaming: bool = True
    chunk_size: int = 4096


class AmbientConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    enabled: bool = True
    model_id: str = "MIT/ast-finetuned-audioset-10-10-0.4593"
    confidence_threshold: float = 0.45
    top_k: int = 2
    min_emit_interval_seconds: float = 1.5


class DBConfig(BaseModel):
    sqlite_path: str = "data/javis_stt.db"


class LoggingConfig(BaseModel):
    dialogue_log_path: str = "logs/dialogue.log"


class AppConfig(BaseModel):
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    ambient: AmbientConfig = Field(default_factory=AmbientConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(path: str) -> AppConfig:
    file_path = Path(path)
    if not file_path.exists():
        return AppConfig()

    with file_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
