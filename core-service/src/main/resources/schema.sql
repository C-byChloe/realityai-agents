-- Schema for RealityAI Core Service

CREATE TABLE IF NOT EXISTS courses (
    course_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description VARCHAR(1000),
    instructor VARCHAR(255) NOT NULL,
    schedule VARCHAR(255),
    location VARCHAR(255),
    credits INT DEFAULT 3,
    semester VARCHAR(20),
    prerequisites VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS students (
    student_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS enrollments (
    id BIGSERIAL PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL REFERENCES students(student_id),
    course_id VARCHAR(20) NOT NULL,
    semester VARCHAR(20),
    grade VARCHAR(5),
    status VARCHAR(20) NOT NULL DEFAULT 'ENROLLED',
    UNIQUE (student_id, course_id, semester)
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id VARCHAR(30) PRIMARY KEY,
    course_id VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description VARCHAR(1000),
    due_date VARCHAR(50),
    max_points INT DEFAULT 100
);

CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_course ON enrollments(course_id);
CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments(course_id);
CREATE INDEX IF NOT EXISTS idx_courses_semester ON courses(semester);
