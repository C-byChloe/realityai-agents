package com.realityai.core.service;

import com.realityai.core.entity.EnrollmentEntity;
import com.realityai.core.entity.StudentEntity;
import com.realityai.core.repository.EnrollmentRepository;
import com.realityai.core.repository.StudentRepository;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class StudentService {

    private final StudentRepository studentRepository;
    private final EnrollmentRepository enrollmentRepository;

    public StudentService(
        StudentRepository studentRepository,
        EnrollmentRepository enrollmentRepository
    ) {
        this.studentRepository = studentRepository;
        this.enrollmentRepository = enrollmentRepository;
    }

    public Optional<StudentEntity> getStudent(String studentId) {
        return studentRepository.findById(studentId);
    }

    @Transactional
    public EnrollmentEntity enrollStudent(String studentId, String courseId, String semester) {
        StudentEntity student = studentRepository.findById(studentId)
            .orElseThrow(() -> new IllegalArgumentException("Student not found: " + studentId));

        EnrollmentEntity enrollment = new EnrollmentEntity(student, courseId, semester);
        return enrollmentRepository.save(enrollment);
    }

    @Transactional
    public boolean dropEnrollment(String studentId, String courseId, String semester) {
        Optional<EnrollmentEntity> enrollment = enrollmentRepository
            .findByStudentStudentIdAndCourseIdAndSemester(studentId, courseId, semester);

        if (enrollment.isEmpty()) {
            return false;
        }

        enrollment.get().setStatus("DROPPED");
        enrollmentRepository.save(enrollment.get());
        return true;
    }

    @Transactional
    public EnrollmentEntity updateGrade(
        String studentId, String courseId, String semester, String grade
    ) {
        EnrollmentEntity enrollment = enrollmentRepository
            .findByStudentStudentIdAndCourseIdAndSemester(studentId, courseId, semester)
            .orElseThrow(() -> new IllegalArgumentException(
                "Enrollment not found: " + studentId + "/" + courseId + "/" + semester
            ));

        enrollment.setGrade(grade);
        return enrollmentRepository.save(enrollment);
    }
}
