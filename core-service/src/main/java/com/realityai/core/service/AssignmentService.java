package com.realityai.core.service;

import com.realityai.core.entity.AssignmentEntity;
import com.realityai.core.repository.AssignmentRepository;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Service;

@Service
public class AssignmentService {

    private final AssignmentRepository assignmentRepository;

    public AssignmentService(AssignmentRepository assignmentRepository) {
        this.assignmentRepository = assignmentRepository;
    }

    public Optional<AssignmentEntity> getAssignment(String assignmentId) {
        return assignmentRepository.findById(assignmentId);
    }

    public List<AssignmentEntity> listAssignments(String courseId) {
        return assignmentRepository.findByCourseId(courseId);
    }

    public AssignmentEntity createAssignment(AssignmentEntity assignment) {
        return assignmentRepository.save(assignment);
    }
}
