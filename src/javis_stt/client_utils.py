from urllib.parse import urlencode


def build_ws_url(base_url: str, session_id: str) -> str:
    trimmed = base_url.rstrip("/")
    query = urlencode({"session_id": session_id})
    return f"{trimmed}/ws/stt?{query}"


def pcm16_bytes_per_second(sample_rate: int, channels: int) -> int:
    return sample_rate * channels * 2
