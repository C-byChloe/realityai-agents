package com.realityai.core.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "assignments")
public class AssignmentEntity {

    @Id
    @Column(name = "assignment_id", nullable = false, length = 30)
    private String assignmentId;

    @Column(name = "course_id", nullable = false, length = 20)
    private String courseId;

    @Column(nullable = false)
    private String title;

    @Column(length = 1000)
    private String description;

    @Column(name = "due_date")
    private String dueDate;

    @Column(name = "max_points")
    private int maxPoints;

    public AssignmentEntity() {}

    public AssignmentEntity(String assignmentId, String courseId, String title) {
        this.assignmentId = assignmentId;
        this.courseId = courseId;
        this.title = title;
    }

    public String getAssignmentId() { return assignmentId; }
    public void setAssignmentId(String assignmentId) { this.assignmentId = assignmentId; }

    public String getCourseId() { return courseId; }
    public void setCourseId(String courseId) { this.courseId = courseId; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getDueDate() { return dueDate; }
    public void setDueDate(String dueDate) { this.dueDate = dueDate; }

    public int getMaxPoints() { return maxPoints; }
    public void setMaxPoints(int maxPoints) { this.maxPoints = maxPoints; }
}
