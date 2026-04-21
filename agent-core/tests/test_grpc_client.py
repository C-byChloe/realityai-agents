"""Tests for gRPC client error handling and mock fallback behavior.

Full integration tests require a running Spring Boot service and are
marked with @pytest.mark.integration. Unit tests verify error handling
and client construction without a live server.
"""

import pytest
import grpc

from grpc_client.client import CoreServiceClient, GrpcServiceError


class TestClientConstruction:
    def test_default_target(self):
        client = CoreServiceClient()
        assert client.target == "localhost:9090"
        assert client.timeout == 10

    def test_custom_target(self):
        client = CoreServiceClient(target="grpc-server:9091", timeout=5)
        assert client.target == "grpc-server:9091"
        assert client.timeout == 5

    def test_close_without_open(self):
        client = CoreServiceClient()
        client.close()  # Should not raise


class TestGrpcServiceError:
    def test_error_attributes(self):
        err = GrpcServiceError(grpc.StatusCode.NOT_FOUND, "Course not found")
        assert err.code == grpc.StatusCode.NOT_FOUND
        assert err.details == "Course not found"
        assert "NOT_FOUND" in str(err)

    def test_error_is_exception(self):
        err = GrpcServiceError(grpc.StatusCode.INTERNAL, "server error")
        assert isinstance(err, Exception)


class TestMockFallback:
    """Verify that tools fall back gracefully when gRPC service is unavailable."""

    def test_grade_update_falls_back(self):
        from agents.action_agent import grade_update
        result = grade_update.invoke({
            "student_id": "S001",
            "course_id": "CS101",
            "assignment_id": "A1",
            "grade": "A",
        })
        assert result["success"] is True
        assert result["mock"] is True

    def test_enrollment_modify_falls_back(self):
        from agents.action_agent import enrollment_modify
        result = enrollment_modify.invoke({
            "student_id": "S001",
            "course_id": "CS201",
            "action": "add",
        })
        assert result["success"] is True
        assert result["mock"] is True

    def test_assignment_create_falls_back(self):
        from agents.action_agent import assignment_create
        result = assignment_create.invoke({
            "course_id": "CS101",
            "title": "Test",
            "due_date": "2026-01-01",
        })
        assert result["success"] is True
        assert result["mock"] is True


@pytest.mark.integration
class TestGrpcIntegration:
    """Integration tests requiring a running Spring Boot gRPC service on localhost:9090."""

    @pytest.fixture
    def client(self):
        c = CoreServiceClient(timeout=5)
        yield c
        c.close()

    def test_get_course(self, client):
        result = client.get_course("CS101")
        assert result["course_id"] == "CS101"
        assert result["name"]

    def test_get_course_not_found(self, client):
        with pytest.raises(grpc.RpcError):
            client.get_course("NONEXISTENT")

    def test_list_courses(self, client):
        result = client.list_courses()
        assert len(result) > 0

    def test_get_student(self, client):
        result = client.get_student("STU001")
        assert result["student_id"] == "STU001"

    def test_enroll_and_drop(self, client):
        enrollment = client.enroll_student("STU001", "CS401", "Spring 2026")
        assert enrollment["course_id"] == "CS401"
        assert enrollment["status"] == "ENROLLED"

        success = client.drop_enrollment("STU001", "CS401", "Spring 2026")
        assert success is True

    def test_create_and_get_assignment(self, client):
        assignment = client.create_assignment({
            "assignment_id": "TEST-001",
            "course_id": "CS101",
            "title": "Integration Test Assignment",
            "due_date": "2026-12-31",
            "max_points": 50,
        })
        assert assignment["assignment_id"] == "TEST-001"

        fetched = client.get_assignment("TEST-001")
        assert fetched["title"] == "Integration Test Assignment"
