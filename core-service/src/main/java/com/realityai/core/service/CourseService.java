package com.realityai.core.service;

import com.realityai.core.entity.CourseEntity;
import com.realityai.core.repository.CourseRepository;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Service;

@Service
public class CourseService {

    private final CourseRepository courseRepository;

    public CourseService(CourseRepository courseRepository) {
        this.courseRepository = courseRepository;
    }

    public Optional<CourseEntity> getCourse(String courseId) {
        return courseRepository.findById(courseId);
    }

    public List<CourseEntity> listCourses(String semester) {
        if (semester != null && !semester.isEmpty()) {
            return courseRepository.findBySemester(semester);
        }
        return courseRepository.findAll();
    }

    public CourseEntity updateCourse(CourseEntity course) {
        return courseRepository.save(course);
    }
}
