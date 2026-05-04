from backend.models import User
from backend.auth.jwt_handler import hash_password


def test_create_user(db):
    """Test that we can create and retrieve a user from the database."""
    user = User(
        email="test@pivot.com",
        hashed_password=hash_password("testpassword123"),
        full_name="Test User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.id is not None
    assert user.email == "test@pivot.com"
    assert user.full_name == "Test User"
    assert user.is_active is True
    assert user.created_at is not None


def test_user_email_unique(db):
    """Test that duplicate emails are rejected."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    user1 = User(email="duplicate@pivot.com", hashed_password="hash1")
    db.add(user1)
    db.commit()

    user2 = User(email="duplicate@pivot.com", hashed_password="hash2")
    db.add(user2)

    with pytest.raises(IntegrityError):
        db.commit()


def test_fetch_user_by_email(db):
    """Test querying users by email."""
    user = User(email="query@pivot.com", hashed_password=hash_password("pass123"))
    db.add(user)
    db.commit()

    fetched = db.query(User).filter(User.email == "query@pivot.com").first()
    assert fetched is not None
    assert fetched.email == "query@pivot.com"
