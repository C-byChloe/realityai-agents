package com.realityai.core.service;

import com.realityai.core.entity.AssignmentEntity;
import com.realityai.core.repository.AssignmentRepository;
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
class AssignmentServiceTest {

    @Autowired
    private AssignmentRepository assignmentRepository;

    private AssignmentService assignmentService;

    @BeforeEach
    void setUp() {
        assignmentRepository.deleteAll();
        assignmentService = new AssignmentService(assignmentRepository);

        AssignmentEntity hw1 = new AssignmentEntity("CS101-HW1", "CS101", "Variables and Types");
        hw1.setMaxPoints(100);
        hw1.setDueDate("2025-09-12");
        assignmentRepository.save(hw1);
    }

    @Test
    void getAssignment_existing() {
        Optional<AssignmentEntity> result = assignmentService.getAssignment("CS101-HW1");
        assertTrue(result.isPresent());
        assertEquals("Variables and Types", result.get().getTitle());
    }

    @Test
    void getAssignment_notFound() {
        Optional<AssignmentEntity> result = assignmentService.getAssignment("NONE");
        assertTrue(result.isEmpty());
    }

    @Test
    void listAssignments_byCourse() {
        List<AssignmentEntity> results = assignmentService.listAssignments("CS101");
        assertEquals(1, results.size());
    }

    @Test
    void listAssignments_noCourse() {
        List<AssignmentEntity> results = assignmentService.listAssignments("CS999");
        assertEquals(0, results.size());
    }

    @Test
    void createAssignment() {
        AssignmentEntity hw2 = new AssignmentEntity("CS101-HW2", "CS101", "Control Flow");
        hw2.setMaxPoints(100);
        AssignmentEntity created = assignmentService.createAssignment(hw2);
        assertEquals("CS101-HW2", created.getAssignmentId());

        List<AssignmentEntity> all = assignmentService.listAssignments("CS101");
        assertEquals(2, all.size());
    }
}
