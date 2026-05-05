package com.realityai.core.service;

import com.realityai.core.entity.EnrollmentEntity;
import com.realityai.core.entity.StudentEntity;
import com.realityai.core.repository.EnrollmentRepository;
import com.realityai.core.repository.StudentRepository;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.test.context.ActiveProfiles;

import static org.junit.jupiter.api.Assertions.*;

@DataJpaTest
@ActiveProfiles("test")
class StudentServiceTest {

    @Autowired
    private StudentRepository studentRepository;

    @Autowired
    private EnrollmentRepository enrollmentRepository;

    private StudentService studentService;

    @BeforeEach
    void setUp() {
        enrollmentRepository.deleteAll();
        studentRepository.deleteAll();
        studentService = new StudentService(studentRepository, enrollmentRepository);

        StudentEntity alice = new StudentEntity("STU001", "Alice", "alice@test.edu");
        studentRepository.save(alice);
    }

    @Test
    void getStudent_existing() {
        Optional<StudentEntity> result = studentService.getStudent("STU001");
        assertTrue(result.isPresent());
        assertEquals("Alice", result.get().getName());
    }

    @Test
    void getStudent_notFound() {
        Optional<StudentEntity> result = studentService.getStudent("STU999");
        assertTrue(result.isEmpty());
    }

    @Test
    void enrollStudent_success() {
        EnrollmentEntity enrollment = studentService.enrollStudent("STU001", "CS101", "Fall 2025");
        assertEquals("CS101", enrollment.getCourseId());
        assertEquals("ENROLLED", enrollment.getStatus());
    }

    @Test
    void enrollStudent_studentNotFound() {
        assertThrows(IllegalArgumentException.class, () ->
            studentService.enrollStudent("STU999", "CS101", "Fall 2025")
        );
    }

    @Test
    void dropEnrollment_success() {
        studentService.enrollStudent("STU001", "CS101", "Fall 2025");
        boolean result = studentService.dropEnrollment("STU001", "CS101", "Fall 2025");
        assertTrue(result);
    }

    @Test
    void dropEnrollment_notFound() {
        boolean result = studentService.dropEnrollment("STU001", "CS999", "Fall 2025");
        assertFalse(result);
    }

    @Test
    void updateGrade_success() {
        studentService.enrollStudent("STU001", "CS101", "Fall 2025");
        EnrollmentEntity updated = studentService.updateGrade("STU001", "CS101", "Fall 2025", "A");
        assertEquals("A", updated.getGrade());
    }

    @Test
    void updateGrade_enrollmentNotFound() {
        assertThrows(IllegalArgumentException.class, () ->
            studentService.updateGrade("STU001", "CS999", "Fall 2025", "A")
        );
    }
}
