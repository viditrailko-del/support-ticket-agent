from sqlalchemy import text


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_tests_use_the_test_database(db):
    # The suite runs against support_test, never the dev database.
    assert db.execute(text("select current_database()")).scalar_one() == "support_test"
