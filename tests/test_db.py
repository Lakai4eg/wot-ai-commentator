import pytest

from wot_ai_commentator.db import WhitelistDB


@pytest.fixture
def db(tmp_path):
    d = WhitelistDB(tmp_path / "wl.db")
    yield d
    d.close()


def test_add_and_get_role(db):
    db.add_user("Viewer1", role="director")
    assert db.get_role("viewer1") == "director"


def test_case_insensitive(db):
    db.add_user("MakeMeFly", role="admin")
    assert db.get_role("makemefly") == "admin"
    assert db.get_role("MAKEMEFLY") == "admin"


def test_unknown_user_is_none(db):
    assert db.get_role("stranger") is None


def test_upsert_changes_role(db):
    db.add_user("u", role="director")
    db.add_user("u", role="admin")
    assert db.get_role("u") == "admin"
    assert len(db.list_users()) == 1


def test_remove(db):
    db.add_user("u")
    assert db.remove_user("U") is True
    assert db.get_role("u") is None
    assert db.remove_user("u") is False


def test_list_users(db):
    db.add_user("a", role="director")
    db.add_user("b", role="admin")
    users = db.list_users()
    assert {u["username"] for u in users} == {"a", "b"}
    assert all(u["platform"] == "twitch" for u in users)
    assert all("added_at" in u for u in users)


def test_invalid_role_raises(db):
    with pytest.raises(ValueError):
        db.add_user("u", role="superuser")


def test_banned_role_stored(db):
    db.add_user("Troll", role="banned")
    assert db.get_role("troll") == "banned"
