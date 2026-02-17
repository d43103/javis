from collections import defaultdict


class SessionManager:
    def __init__(self):
        self._counters = defaultdict(int)

    def next_segment_id(self, session_id: str) -> str:
        self._counters[session_id] += 1
        return f"seg-{self._counters[session_id]:06d}"
