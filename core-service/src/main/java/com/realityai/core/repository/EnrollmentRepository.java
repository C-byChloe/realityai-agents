package com.realityai.core.repository;

import com.realityai.core.entity.EnrollmentEntity;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface EnrollmentRepository extends JpaRepository<EnrollmentEntity, Long> {
    List<EnrollmentEntity> findByStudentStudentId(String studentId);

    Optional<EnrollmentEntity> findByStudentStudentIdAndCourseIdAndSemester(
        String studentId, String courseId, String semester
    );
}
