-- Seed data for RealityAI Core Service
-- Uses MERGE/ON CONFLICT to be idempotent

INSERT INTO courses (course_id, name, description, instructor, schedule, location, credits, semester, prerequisites)
VALUES
    ('CS101', 'Introduction to Computer Science', 'Foundational course covering programming fundamentals, computational thinking, and problem solving using Python.', 'Dr. Smith', 'MWF 10:00-10:50 AM', 'Science Hall 201', 3, 'Fall 2025', ''),
    ('CS201', 'Data Structures and Algorithms', 'Arrays, linked lists, trees, graphs, sorting, and searching algorithms with complexity analysis.', 'Dr. Johnson', 'TTh 2:00-3:15 PM', 'Engineering 305', 3, 'Fall 2025', 'CS101'),
    ('MATH200', 'Linear Algebra', 'Vectors, matrices, linear transformations, eigenvalues, and applications.', 'Dr. Williams', 'MW 1:00-2:15 PM', 'Math Building 110', 4, 'Fall 2025', ''),
    ('CS401', 'Machine Learning', 'Supervised and unsupervised learning, neural networks, and practical ML applications.', 'Dr. Chen', 'TTh 10:00-11:15 AM', 'Engineering 401', 3, 'Fall 2025', 'CS201,MATH200')
ON CONFLICT (course_id) DO NOTHING;

INSERT INTO students (student_id, name, email)
VALUES
    ('STU001', 'Alice Martinez', 'alice.martinez@university.edu'),
    ('STU002', 'Bob Chen', 'bob.chen@university.edu'),
    ('STU003', 'Carol Williams', 'carol.williams@university.edu'),
    ('STU004', 'David Kim', 'david.kim@university.edu'),
    ('STU005', 'Eve Johnson', 'eve.johnson@university.edu')
ON CONFLICT (student_id) DO NOTHING;

INSERT INTO enrollments (student_id, course_id, semester, grade, status)
VALUES
    ('STU001', 'CS101', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU001', 'MATH200', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU002', 'CS101', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU002', 'CS201', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU003', 'CS201', 'Fall 2025', 'A', 'COMPLETED'),
    ('STU003', 'MATH200', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU004', 'CS401', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU005', 'CS101', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU005', 'CS201', 'Fall 2025', NULL, 'ENROLLED'),
    ('STU005', 'MATH200', 'Fall 2025', NULL, 'ENROLLED')
ON CONFLICT (student_id, course_id, semester) DO NOTHING;

INSERT INTO assignments (assignment_id, course_id, title, description, due_date, max_points)
VALUES
    ('CS101-HW1', 'CS101', 'Variables and Types', 'Practice with Python variables, data types, and basic operations.', '2025-09-12', 100),
    ('CS101-HW2', 'CS101', 'Control Flow', 'Implement loops, conditionals, and basic control structures.', '2025-09-19', 100),
    ('CS201-HW1', 'CS201', 'Linked List Implementation', 'Implement a singly and doubly linked list with standard operations.', '2025-09-15', 150),
    ('CS201-HW2', 'CS201', 'Binary Search Tree', 'Implement BST with insert, delete, and traversal operations.', '2025-09-29', 150),
    ('MATH200-HW1', 'MATH200', 'Matrix Operations', 'Matrix multiplication, determinants, and row reduction exercises.', '2025-09-14', 100),
    ('CS401-HW1', 'CS401', 'Linear Regression', 'Implement linear regression from scratch and compare with scikit-learn.', '2025-09-20', 200)
ON CONFLICT (assignment_id) DO NOTHING;
