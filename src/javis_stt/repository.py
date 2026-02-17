from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AITurn, Transcript


class TranscriptRepository:
    def __init__(self, session: Session):
        self.session = session

    def save_partial(
        self,
        session_id: str,
        segment_id: str,
        started_at: float,
        ended_at: float,
        text: str,
        confidence: float | None = None,
    ) -> Transcript:
        row = Transcript(
            session_id=session_id,
            segment_id=segment_id,
            event_type="partial",
            started_at=started_at,
            ended_at=ended_at,
            text=text,
            confidence=confidence,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_final(
        self,
        session_id: str,
        segment_id: str,
        started_at: float,
        ended_at: float,
        text: str,
        confidence: float | None = None,
    ) -> Transcript:
        row = Transcript(
            session_id=session_id,
            segment_id=segment_id,
            event_type="final",
            started_at=started_at,
            ended_at=ended_at,
            text=text,
            confidence=confidence,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_ambient(
        self,
        session_id: str,
        segment_id: str,
        started_at: float,
        ended_at: float,
        text: str,
        confidence: float | None = None,
    ) -> Transcript:
        row = Transcript(
            session_id=session_id,
            segment_id=segment_id,
            event_type="ambient",
            started_at=started_at,
            ended_at=ended_at,
            text=text,
            confidence=confidence,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_finals(self, session_id: str) -> list[Transcript]:
        query = (
            select(Transcript)
            .where(Transcript.session_id == session_id)
            .where(Transcript.event_type == "final")
            .order_by(Transcript.segment_id.asc(), Transcript.created_at.asc())
        )
        return list(self.session.scalars(query).all())

    def save_ai_turn(
        self,
        session_id: str,
        segment_id: str,
        request_text: str,
        response_text: str,
        error: str | None = None,
    ) -> AITurn:
        row = AITurn(
            session_id=session_id,
            segment_id=segment_id,
            request_text=request_text,
            response_text=response_text,
            error=error,
        )
        self.session.add(row)
        self.session.flush()
        return row
