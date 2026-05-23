import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store


def main() -> int:
    _configure_stdio()
    _test_missing_sqlite_is_created()
    _test_existing_sqlite_uses_lightweight_probe()
    _test_unrelated_text_file_is_ignored()
    print("startup SQLite state smoke tests passed")
    return 0


def _test_missing_sqlite_is_created() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            created_path = state_store.initialize_sqlite_state()
            summary = db_store.get_sqlite_state_summary(paths["db_path"])

        _assert(created_path == paths["db_path"], "startup did not initialize the expected DB path")
        _assert(paths["db_path"].exists(), "startup did not create SQLite DB")
        _assert("error" not in summary, f"new SQLite DB summary has error: {summary.get('error')}")
        _assert(summary["download_items"] == 0, "new SQLite DB should start empty")


def _test_existing_sqlite_uses_lightweight_probe() -> None:
    with _temp_runtime() as paths:
        _seed_sqlite_many_rows(paths["db_path"], rows=1000)
        recorder = _SqlRecorder(db_store.sqlite3.connect)
        with _patched_db_file(paths["db_path"]), _patched_sqlite_connect(recorder):
            state_store.initialize_sqlite_state()

        statements = recorder.statements_text()
        _assert("CREATE TABLE IF NOT EXISTS download_items" in statements, "startup did not ensure schema")
        _assert("COUNT(" not in statements.upper(), "startup used COUNT(*) for initialization")


def _test_unrelated_text_file_is_ignored() -> None:
    with _temp_runtime() as paths:
        unrelated = paths["data_dir"] / "old_state.txt"
        unrelated.write_text("not used", encoding="utf-8")
        with _patched_db_file(paths["db_path"]):
            state_store.initialize_sqlite_state()
            _assert(unrelated.read_text(encoding="utf-8") == "not used", "startup touched unrelated text file")


def _seed_sqlite_many_rows(path: Path, rows: int) -> None:
    db_store.init_db(path)
    now = "2026-01-01T00:00:00+00:00"
    with closing(db_store.connect_db(path)) as conn:
        channel_id = conn.execute(
            """
            INSERT INTO channels(
                platform,
                channel_id,
                channel_name,
                save_base_folder_raw,
                save_base_folder_norm,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("youtube", "channel", "Channel", "D:/Out", "d:/out", now, now),
        ).lastrowid
        conn.executemany(
            """
            INSERT INTO download_items(
                channel_db_id,
                platform,
                channel_id,
                video_id,
                save_base_folder_raw,
                save_base_folder_norm,
                sanitized_filename_base,
                status,
                updated_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    channel_id,
                    "youtube",
                    "channel",
                    f"video-{index}",
                    "D:/Out",
                    "d:/out",
                    f"video-{index}",
                    state_store.STATUS_NOT_DOWNLOADED,
                    now,
                    now,
                )
                for index in range(rows)
            ),
        )
        conn.commit()


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        yield {
            "data_dir": data_dir,
            "db_path": data_dir / "download_state.sqlite3",
        }


@contextmanager
def _patched_db_file(db_path: Path):
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    try:
        state_store.db_file = lambda: db_path
        db_store.db_file = lambda: db_path
        yield
    finally:
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file


@contextmanager
def _patched_sqlite_connect(recorder):
    old_connect = db_store.sqlite3.connect
    try:
        db_store.sqlite3.connect = recorder.connect
        yield
    finally:
        db_store.sqlite3.connect = old_connect


class _SqlRecorder:
    def __init__(self, connect):
        self._connect = connect
        self.statements = []

    def connect(self, *args, **kwargs):
        return _ConnectionProxy(self._connect(*args, **kwargs), self.statements)

    def statements_text(self) -> str:
        return "\n".join(self.statements)


class _ConnectionProxy:
    def __init__(self, conn, statements):
        self._conn = conn
        self._statements = statements

    def execute(self, sql, parameters=(), /):
        self._statements.append(" ".join(str(sql).split()))
        return self._conn.execute(sql, parameters)

    def executescript(self, sql):
        self._statements.append(" ".join(str(sql).split()))
        return self._conn.executescript(sql)

    def close(self):
        return self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._conn.__exit__(exc_type, exc, traceback)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
