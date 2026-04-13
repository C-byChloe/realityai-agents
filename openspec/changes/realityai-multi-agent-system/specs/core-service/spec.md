## ADDED Requirements

### Requirement: Spring Boot gRPC service for course management
The system SHALL implement a Spring Boot application on port 8080 exposing gRPC endpoints for course, student, and assignment CRUD operations using Protocol Buffer contracts.

#### Scenario: Course lookup via gRPC
- **WHEN** the agent core sends a GetCourse gRPC request with a course_id
- **THEN** the Spring Boot service returns the course details from PostgreSQL

#### Scenario: Grade update via gRPC
- **WHEN** the agent core sends an UpdateGrade gRPC request with student_id, course_id, and new grade
- **THEN** the Spring Boot service updates the grade in PostgreSQL and returns a success response

### Requirement: Protocol Buffer schema definitions
The system SHALL define shared .proto files in the /proto directory for course, student, and assignment entities. Both Python and Java codegen SHALL be supported.

#### Scenario: Proto compilation succeeds for both languages
- **WHEN** the proto compilation script runs
- **THEN** Python gRPC stubs and Java gRPC service interfaces are generated without errors

### Requirement: JPA entities with PostgreSQL persistence
The system SHALL use JPA entities for Course, Student, and Assignment with PostgreSQL as the backing database. Schema migrations SHALL be managed.

#### Scenario: Seed data is loaded
- **WHEN** the Spring Boot service starts with an empty database
- **THEN** seed data (sample courses, students, assignments) is loaded via migration scripts

### Requirement: gRPC error handling
The system SHALL return appropriate gRPC status codes for error conditions (NOT_FOUND, INVALID_ARGUMENT, INTERNAL).

#### Scenario: Course not found
- **WHEN** a GetCourse request is made with a non-existent course_id
- **THEN** the service returns gRPC status NOT_FOUND
