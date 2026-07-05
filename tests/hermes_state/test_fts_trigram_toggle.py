"""Tests for the sessions.fts_trigram toggle (SessionDB trigram teardown).

The trigram FTS index is a full second copy of message text used for
CJK/substring recall. It can be the single largest object in state.db. These
tests cover the opt-out path: sessions.fts_trigram=false must drop the trigram
tables + triggers, reclaim the freed pages, leave the base word index intact,
be idempotent across reopens, and be reversible when re-enabled.
"""
import sqlite3

import pytest

import hermes_state
from hermes_state import SessionDB


def _fts_objects(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'messages_fts%'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _seed(db, sid="s1", n=40):
    db.create_session(sid, source="cli")
    for i in range(n):
        db.append_message(sid, role="user", content=f"hello world substring {i}")


@pytest.fixture
def dbfile(tmp_path):
    return tmp_path / "state.db"


def _set_trigram(monkeypatch, enabled):
    monkeypatch.setattr(hermes_state, "_trigram_enabled", lambda: enabled)


class TestTrigramToggle:
    def test_default_creates_trigram(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        _seed(db)
        db.close()
        objs = _fts_objects(dbfile)
        assert "messages_fts_trigram" in objs
        assert "messages_fts" in objs

    def test_disable_tears_down_trigram_keeps_base(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        _seed(db)
        db.close()
        assert "messages_fts_trigram" in _fts_objects(dbfile)

        # Reopen with trigram disabled -> teardown
        _set_trigram(monkeypatch, False)
        db2 = SessionDB(dbfile)
        db2.close()
        objs = _fts_objects(dbfile)
        assert "messages_fts_trigram" not in objs
        # base word index and its triggers survive
        assert "messages_fts" in objs
        conn = sqlite3.connect(str(dbfile))
        try:
            trg = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert not any("trigram" in t for t in trg)
        assert "messages_fts_insert" in trg

    def test_base_search_survives_teardown(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        _seed(db)
        db.close()
        _set_trigram(monkeypatch, False)
        db2 = SessionDB(dbfile)
        try:
            n = db2._conn.execute(
                "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'hello'"
            ).fetchone()[0]
        finally:
            db2.close()
        assert n > 0

    def test_teardown_reclaims_space(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        db.create_session("big", source="cli")
        blob = "lorem ipsum dolor sit amet " * 40
        for i in range(1500):
            db.append_message("big", role="user", content=f"{i} {blob}")
        db.close()
        size_on = dbfile.stat().st_size

        _set_trigram(monkeypatch, False)
        SessionDB(dbfile).close()
        size_off = dbfile.stat().st_size
        assert size_off < size_on  # VACUUM after teardown actually shrinks file

    def test_disable_is_idempotent(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        _seed(db)
        db.close()
        _set_trigram(monkeypatch, False)
        SessionDB(dbfile).close()
        # second disabled open must not raise and must stay torn down
        SessionDB(dbfile).close()
        assert "messages_fts_trigram" not in _fts_objects(dbfile)

    def test_reenable_restores_trigram(self, dbfile, monkeypatch):
        _set_trigram(monkeypatch, True)
        db = SessionDB(dbfile)
        _seed(db)
        db.close()
        _set_trigram(monkeypatch, False)
        SessionDB(dbfile).close()
        assert "messages_fts_trigram" not in _fts_objects(dbfile)
        # Re-enable -> recreated and backfilled
        _set_trigram(monkeypatch, True)
        db2 = SessionDB(dbfile)
        try:
            n = db2._conn.execute(
                "SELECT COUNT(*) FROM messages_fts_trigram "
                "WHERE messages_fts_trigram MATCH 'hello'"
            ).fetchone()[0]
        finally:
            db2.close()
        assert "messages_fts_trigram" in _fts_objects(dbfile)
        assert n > 0
