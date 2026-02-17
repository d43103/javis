from src.javis_stt.repository import TranscriptRepository


def test_insert_final_segment_preserves_order(db_session):
    repo = TranscriptRepository(db_session)
    repo.save_final("s1", "seg-001", 0.0, 1.0, "안녕하세요")
    repo.save_final("s1", "seg-002", 1.0, 2.0, "테스트입니다")
    db_session.commit()

    rows = repo.list_finals("s1")

    assert [r.segment_id for r in rows] == ["seg-001", "seg-002"]
