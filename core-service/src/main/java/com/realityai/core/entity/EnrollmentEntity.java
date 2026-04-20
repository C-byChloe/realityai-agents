package com.realityai.core.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

@Entity
@Table(
    name = "enrollments",
    uniqueConstraints = @UniqueConstraint(
        columnNames = {"student_id", "course_id", "semester"}
    )
)
public class EnrollmentEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "student_id", nullable = false)
    private StudentEntity student;

    @Column(name = "course_id", nullable = false, length = 20)
    private String courseId;

    @Column(length = 20)
    private String semester;

    @Column(length = 5)
    private String grade;

    @Column(nullable = false, length = 20)
    private String status; // ENROLLED, DROPPED, COMPLETED

    public EnrollmentEntity() {}

    public EnrollmentEntity(StudentEntity student, String courseId, String semester) {
        this.student = student;
        this.courseId = courseId;
        this.semester = semester;
        this.status = "ENROLLED";
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public StudentEntity getStudent() { return student; }
    public void setStudent(StudentEntity student) { this.student = student; }

    public String getCourseId() { return courseId; }
    public void setCourseId(String courseId) { this.courseId = courseId; }

    public String getSemester() { return semester; }
    public void setSemester(String semester) { this.semester = semester; }

    public String getGrade() { return grade; }
    public void setGrade(String grade) { this.grade = grade; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
}
