from pathlib import Path
import sys
from collections.abc import Iterator
import importlib

import pytest
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

create_sqlite_engine = importlib.import_module("src.javis_stt.db").create_sqlite_engine
Base = importlib.import_module("src.javis_stt.models").Base


@pytest.fixture
def db_session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "test.db"
    engine = create_sqlite_engine(str(db_path))
    Base.metadata.create_all(engine)

    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
