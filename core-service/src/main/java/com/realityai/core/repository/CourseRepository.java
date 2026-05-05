package com.realityai.core.repository;

import com.realityai.core.entity.CourseEntity;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CourseRepository extends JpaRepository<CourseEntity, String> {
    List<CourseEntity> findBySemester(String semester);
}
