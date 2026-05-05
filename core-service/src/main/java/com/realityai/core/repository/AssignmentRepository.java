package com.realityai.core.repository;

import com.realityai.core.entity.AssignmentEntity;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AssignmentRepository extends JpaRepository<AssignmentEntity, String> {
    List<AssignmentEntity> findByCourseId(String courseId);
}
