package com.realityai.core.service;

import com.realityai.core.entity.CourseEntity;
import com.realityai.core.repository.CourseRepository;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.test.context.ActiveProfiles;

import static org.junit.jupiter.api.Assertions.*;

@DataJpaTest
@ActiveProfiles("test")
class CourseServiceTest {

    @Autowired
    private CourseRepository courseRepository;

    private CourseService courseService;

    @BeforeEach
    void setUp() {
        courseRepository.deleteAll();
        courseService = new CourseService(courseRepository);

        CourseEntity cs101 = new CourseEntity("CS101", "Intro to CS", "Dr. Smith");
        cs101.setSemester("Fall 2025");
        cs101.setCredits(3);
        courseRepository.save(cs101);

        CourseEntity cs201 = new CourseEntity("CS201", "Data Structures", "Dr. Johnson");
        cs201.setSemester("Fall 2025");
        cs201.setCredits(3);
        courseRepository.save(cs201);
    }

    @Test
    void getCourse_existing() {
        Optional<CourseEntity> result = courseService.getCourse("CS101");
        assertTrue(result.isPresent());
        assertEquals("Intro to CS", result.get().getName());
    }

    @Test
    void getCourse_notFound() {
        Optional<CourseEntity> result = courseService.getCourse("CS999");
        assertTrue(result.isEmpty());
    }

    @Test
    void listCourses_all() {
        List<CourseEntity> courses = courseService.listCourses(null);
        assertEquals(2, courses.size());
    }

    @Test
    void listCourses_bySemester() {
        List<CourseEntity> courses = courseService.listCourses("Fall 2025");
        assertEquals(2, courses.size());
    }

    @Test
    void listCourses_noMatch() {
        List<CourseEntity> courses = courseService.listCourses("Spring 2026");
        assertEquals(0, courses.size());
    }

    @Test
    void updateCourse() {
        CourseEntity course = courseService.getCourse("CS101").get();
        course.setInstructor("Dr. Jones");
        CourseEntity updated = courseService.updateCourse(course);
        assertEquals("Dr. Jones", updated.getInstructor());
    }
}
