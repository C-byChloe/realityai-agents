"""Tests for JWT authentication."""

import pytest
from fastapi import HTTPException

from auth.jwt import create_access_token, verify_token, TokenData


class TestCreateToken:
    def test_creates_token_string(self):
        token = create_access_token("user1", "student")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token("user1", "student")
        t2 = create_access_token("user2", "student")
        assert t1 != t2


class TestVerifyToken:
    def test_valid_token_returns_data(self):
        token = create_access_token("user1", "instructor")
        data = verify_token(token)
        assert data.user_id == "user1"
        assert data.role == "instructor"

    def test_student_role(self):
        token = create_access_token("stu001", "student")
        data = verify_token(token)
        assert data.role == "student"

    def test_invalid_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_token("invalid.token.here")
        assert exc_info.value.status_code == 401

    def test_empty_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_token("")
        assert exc_info.value.status_code == 401
