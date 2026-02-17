from src.javis_stt.config import load_config


def test_load_config_defaults(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text("stt:\n  model_size: large-v3\n", encoding="utf-8")

    cfg = load_config(str(cfg_path))

    assert cfg.stt.model_size == "large-v3"
    assert cfg.stt.language == "ko"
    assert cfg.stt.beam_size == 5


def test_ai_config_defaults(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    cfg = load_config(str(cfg_path))

    assert cfg.ai.api_format == "ollama"
    assert cfg.ai.system_prompt == ""
    assert cfg.ai.enable_thinking is False


def test_ai_config_openai_format(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text(
        "ai:\n  api_format: openai\n  base_url: http://127.0.0.1:8041\n  model: Qwen/Qwen3-14B-AWQ\n",
        encoding="utf-8",
    )

    cfg = load_config(str(cfg_path))

    assert cfg.ai.api_format == "openai"
    assert cfg.ai.base_url == "http://127.0.0.1:8041"
    assert cfg.ai.model == "Qwen/Qwen3-14B-AWQ"


def test_tts_config_defaults(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text("{}", encoding="utf-8")

    cfg = load_config(str(cfg_path))

    assert cfg.tts.enabled is False
    assert cfg.tts.provider == "vllm"
    assert cfg.tts.base_url == "http://127.0.0.1:8031"
    assert cfg.tts.model == "Qwen/Qwen3-TTS-0.6B"
    assert cfg.tts.voice == "Sohee"
    assert cfg.tts.sample_rate == 24000
    assert cfg.tts.streaming is True
    assert cfg.tts.chunk_size == 4096


def test_tts_config_enabled(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text(
        "tts:\n  enabled: true\n  voice: Chelsie\n  sample_rate: 16000\n",
        encoding="utf-8",
    )

    cfg = load_config(str(cfg_path))

    assert cfg.tts.enabled is True
    assert cfg.tts.voice == "Chelsie"
    assert cfg.tts.sample_rate == 16000


def test_backward_compat_no_tts_section(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text(
        "stt:\n  model_size: turbo\nai:\n  enabled: true\n",
        encoding="utf-8",
    )

    cfg = load_config(str(cfg_path))

    assert cfg.tts.enabled is False
    assert cfg.ai.api_format == "ollama"


def test_tts_config_has_voice_clone_fields():
    from src.javis_stt.config import TTSConfig
    cfg = TTSConfig(
        enabled=True,
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        voice="test01",
        voice_clone_ref_audio="recordings/test-01.wav",
        voice_clone_ref_text="처리하고 합니다.",
    )
    assert cfg.voice_clone_ref_audio == "recordings/test-01.wav"
    assert cfg.voice_clone_ref_text == "처리하고 합니다."


def test_ai_config_disabled_and_tts_voice_clone_yaml(tmp_path):
    from src.javis_stt.config import load_config
    yaml_content = """
ai:
  enabled: false
tts:
  enabled: true
  voice_clone_ref_audio: recordings/test-01.wav
  voice_clone_ref_text: "처리하고 합니다."
"""
    p = tmp_path / "stt.yaml"
    p.write_text(yaml_content)
    cfg = load_config(str(p))
    assert cfg.ai.enabled is False
    assert cfg.tts.voice_clone_ref_audio == "recordings/test-01.wav"
    assert cfg.tts.voice_clone_ref_text == "처리하고 합니다."
