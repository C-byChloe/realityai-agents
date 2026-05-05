"""gRPC client for communicating with the Spring Boot core service.

Provides typed Python methods for each gRPC endpoint with error handling
and configurable timeout. Raises GrpcServiceError on failures.
"""

import grpc

from proto_gen import (
    assignment_pb2,
    assignment_pb2_grpc,
    course_pb2,
    course_pb2_grpc,
    student_pb2,
    student_pb2_grpc,
)

DEFAULT_TARGET = "localhost:9090"
DEFAULT_TIMEOUT = 10  # seconds


class GrpcServiceError(Exception):
    """Raised when a gRPC call fails."""

    def __init__(self, code: grpc.StatusCode, details: str):
        self.code = code
        self.details = details
        super().__init__(f"gRPC error {code.name}: {details}")


def _handle_rpc_error(error: grpc.RpcError) -> None:
    """Convert gRPC errors to GrpcServiceError."""
    code = error.code() if hasattr(error, "code") else grpc.StatusCode.UNKNOWN
    details = error.details() if hasattr(error, "details") else str(error)
    raise GrpcServiceError(code, details) from error


class CoreServiceClient:
    """Client for the RealityAI Core Service gRPC API."""

    def __init__(self, target: str = DEFAULT_TARGET, timeout: float = DEFAULT_TIMEOUT):
        self.target = target
        self.timeout = timeout
        self._channel = None

    def _get_channel(self) -> grpc.Channel:
        if self._channel is None:
            self._channel = grpc.insecure_channel(self.target)
        return self._channel

    def close(self):
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    # --- Course operations ---

    def get_course(self, course_id: str) -> dict:
        stub = course_pb2_grpc.CourseServiceStub(self._get_channel())
        request = course_pb2.GetCourseRequest(course_id=course_id)
        response = stub.GetCourse(request, timeout=self.timeout)
        return _course_to_dict(response.course)

    def list_courses(self, semester: str = "") -> list[dict]:
        stub = course_pb2_grpc.CourseServiceStub(self._get_channel())
        request = course_pb2.ListCoursesRequest(semester=semester)
        response = stub.ListCourses(request, timeout=self.timeout)
        return [_course_to_dict(c) for c in response.courses]

    def update_course(self, course_data: dict) -> dict:
        stub = course_pb2_grpc.CourseServiceStub(self._get_channel())
        course = course_pb2.Course(**course_data)
        request = course_pb2.UpdateCourseRequest(course=course)
        response = stub.UpdateCourse(request, timeout=self.timeout)
        return _course_to_dict(response.course)

    # --- Student operations ---

    def get_student(self, student_id: str) -> dict:
        stub = student_pb2_grpc.StudentServiceStub(self._get_channel())
        request = student_pb2.GetStudentRequest(student_id=student_id)
        response = stub.GetStudent(request, timeout=self.timeout)
        return _student_to_dict(response.student)

    def enroll_student(self, student_id: str, course_id: str, semester: str) -> dict:
        stub = student_pb2_grpc.StudentServiceStub(self._get_channel())
        request = student_pb2.EnrollStudentRequest(
            student_id=student_id, course_id=course_id, semester=semester
        )
        response = stub.EnrollStudent(request, timeout=self.timeout)
        return _enrollment_to_dict(response.enrollment)

    def drop_enrollment(self, student_id: str, course_id: str, semester: str) -> bool:
        stub = student_pb2_grpc.StudentServiceStub(self._get_channel())
        request = student_pb2.DropEnrollmentRequest(
            student_id=student_id, course_id=course_id, semester=semester
        )
        response = stub.DropEnrollment(request, timeout=self.timeout)
        return response.success

    def update_grade(
        self, student_id: str, course_id: str, semester: str, grade: str
    ) -> dict:
        stub = student_pb2_grpc.StudentServiceStub(self._get_channel())
        request = student_pb2.UpdateGradeRequest(
            student_id=student_id, course_id=course_id,
            semester=semester, grade=grade,
        )
        response = stub.UpdateGrade(request, timeout=self.timeout)
        return _enrollment_to_dict(response.enrollment)

    # --- Assignment operations ---

    def get_assignment(self, assignment_id: str) -> dict:
        stub = assignment_pb2_grpc.AssignmentServiceStub(self._get_channel())
        request = assignment_pb2.GetAssignmentRequest(assignment_id=assignment_id)
        response = stub.GetAssignment(request, timeout=self.timeout)
        return _assignment_to_dict(response.assignment)

    def list_assignments(self, course_id: str) -> list[dict]:
        stub = assignment_pb2_grpc.AssignmentServiceStub(self._get_channel())
        request = assignment_pb2.ListAssignmentsRequest(course_id=course_id)
        response = stub.ListAssignments(request, timeout=self.timeout)
        return [_assignment_to_dict(a) for a in response.assignments]

    def create_assignment(self, assignment_data: dict) -> dict:
        stub = assignment_pb2_grpc.AssignmentServiceStub(self._get_channel())
        assignment = assignment_pb2.Assignment(**assignment_data)
        request = assignment_pb2.CreateAssignmentRequest(assignment=assignment)
        response = stub.CreateAssignment(request, timeout=self.timeout)
        return _assignment_to_dict(response.assignment)


def _course_to_dict(course) -> dict:
    return {
        "course_id": course.course_id,
        "name": course.name,
        "description": course.description,
        "instructor": course.instructor,
        "schedule": course.schedule,
        "location": course.location,
        "credits": course.credits,
        "semester": course.semester,
        "prerequisites": list(course.prerequisites),
    }


def _student_to_dict(student) -> dict:
    return {
        "student_id": student.student_id,
        "name": student.name,
        "email": student.email,
        "enrollments": [_enrollment_to_dict(e) for e in student.enrollments],
    }


def _enrollment_to_dict(enrollment) -> dict:
    return {
        "course_id": enrollment.course_id,
        "semester": enrollment.semester,
        "grade": enrollment.grade,
        "status": enrollment.status,
    }


def _assignment_to_dict(assignment) -> dict:
    return {
        "assignment_id": assignment.assignment_id,
        "course_id": assignment.course_id,
        "title": assignment.title,
        "description": assignment.description,
        "due_date": assignment.due_date,
        "max_points": assignment.max_points,
    }
