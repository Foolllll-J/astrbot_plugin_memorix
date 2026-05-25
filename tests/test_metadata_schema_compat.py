import sqlite3

from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore, SCHEMA_VERSION


def test_connect_patches_legacy_episode_position_column(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            paragraph_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE episode_paragraphs (
            episode_id TEXT NOT NULL,
            paragraph_hash TEXT NOT NULL,
            PRIMARY KEY (episode_id, paragraph_hash)
        );
        INSERT INTO paragraphs(hash, content, created_at, updated_at, source, word_count, is_deleted)
        VALUES ('p1', 'hello', 1.0, 1.0, 'source-a', 1, 0);
        INSERT INTO episodes(episode_id, source, title, summary, paragraph_count, created_at, updated_at)
        VALUES ('e1', 'source-a', 'title', 'summary', 1, 1.0, 1.0);
        INSERT INTO episode_paragraphs(episode_id, paragraph_hash) VALUES ('e1', 'p1');
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
        assert store.get_episode_paragraphs("e1")[0]["position"] == 0
    finally:
        store.close()


def test_connect_patches_legacy_transcript_position_column(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE entities (
            hash TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            vector_index INTEGER,
            appearance_count INTEGER DEFAULT 1,
            created_at REAL,
            metadata TEXT,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            vector_index INTEGER,
            confidence REAL DEFAULT 1.0,
            created_at REAL,
            source_paragraph TEXT,
            metadata TEXT
        );
        CREATE TABLE transcript_sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            metadata_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE transcript_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at REAL NOT NULL
        );
        INSERT INTO transcript_sessions(session_id, source, metadata_json, created_at, updated_at)
        VALUES ('s1', 'chat', '{{}}', 1.0, 1.0);
        INSERT INTO transcript_messages(session_id, role, content, metadata_json, created_at)
        VALUES ('s1', 'user', 'hello', '{{}}', 1.0);
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(transcript_messages)").fetchall()}
        assert "position" in columns
        assert store.get_transcript_messages("s1")[0]["position"] == 0
    finally:
        store.close()


def test_transcript_summary_state_cursor_roundtrip(tmp_path):
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        store.upsert_transcript_session(session_id="s1", source="chat", metadata={})
        store.append_transcript_messages(
            session_id="s1",
            messages=[
                {"role": "user", "content": "hello", "created_at": 10.0},
                {"role": "assistant", "content": "hi", "created_at": 11.0},
            ],
        )

        state = store.mark_transcript_summary_complete(
            session_id="s1",
            task_id="task-1",
            metadata={"trigger": "test"},
        )

        assert state["session_id"] == "s1"
        assert state["last_task_id"] == "task-1"
        assert state["last_message_created_at"] == 11.0
        assert state["summary_count"] == 1
        assert state["metadata"]["trigger"] == "test"

        second = store.mark_transcript_summary_complete(session_id="s1", last_message_created_at=12.0)
        assert second["summary_count"] == 2
        assert second["last_message_created_at"] == 12.0
    finally:
        store.close()


def test_existing_version_db_still_gets_episode_position_patch(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            paragraph_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE episode_paragraphs (
            episode_id TEXT NOT NULL,
            paragraph_hash TEXT NOT NULL,
            PRIMARY KEY (episode_id, paragraph_hash)
        );
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
    finally:
        store.close()

    reopened = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in reopened.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
    finally:
        reopened.close()
