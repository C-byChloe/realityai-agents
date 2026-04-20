package com.realityai.core.grpc;

import com.realityai.core.entity.EnrollmentEntity;
import com.realityai.core.entity.StudentEntity;
import com.realityai.core.service.StudentService;
import com.realityai.proto.DropEnrollmentRequest;
import com.realityai.proto.DropEnrollmentResponse;
import com.realityai.proto.Enrollment;
import com.realityai.proto.EnrollStudentRequest;
import com.realityai.proto.EnrollStudentResponse;
import com.realityai.proto.GetStudentRequest;
import com.realityai.proto.GetStudentResponse;
import com.realityai.proto.Student;
import com.realityai.proto.StudentServiceGrpc;
import com.realityai.proto.UpdateGradeRequest;
import com.realityai.proto.UpdateGradeResponse;
import io.grpc.Status;
import io.grpc.stub.StreamObserver;

public class StudentGrpcService extends StudentServiceGrpc.StudentServiceImplBase {

    private final StudentService studentService;

    public StudentGrpcService(StudentService studentService) {
        this.studentService = studentService;
    }

    @Override
    public void getStudent(GetStudentRequest request, StreamObserver<GetStudentResponse> responseObserver) {
        studentService.getStudent(request.getStudentId())
            .ifPresentOrElse(
                entity -> {
                    responseObserver.onNext(
                        GetStudentResponse.newBuilder()
                            .setStudent(toProto(entity))
                            .build()
                    );
                    responseObserver.onCompleted();
                },
                () -> responseObserver.onError(
                    Status.NOT_FOUND
                        .withDescription("Student not found: " + request.getStudentId())
                        .asRuntimeException()
                )
            );
    }

    @Override
    public void enrollStudent(EnrollStudentRequest request, StreamObserver<EnrollStudentResponse> responseObserver) {
        try {
            EnrollmentEntity enrollment = studentService.enrollStudent(
                request.getStudentId(), request.getCourseId(), request.getSemester()
            );
            responseObserver.onNext(
                EnrollStudentResponse.newBuilder()
                    .setEnrollment(toEnrollmentProto(enrollment))
                    .build()
            );
            responseObserver.onCompleted();
        } catch (IllegalArgumentException e) {
            responseObserver.onError(
                Status.NOT_FOUND.withDescription(e.getMessage()).asRuntimeException()
            );
        }
    }

    @Override
    public void dropEnrollment(DropEnrollmentRequest request, StreamObserver<DropEnrollmentResponse> responseObserver) {
        boolean success = studentService.dropEnrollment(
            request.getStudentId(), request.getCourseId(), request.getSemester()
        );
        if (!success) {
            responseObserver.onError(
                Status.NOT_FOUND
                    .withDescription("Enrollment not found")
                    .asRuntimeException()
            );
            return;
        }
        responseObserver.onNext(
            DropEnrollmentResponse.newBuilder().setSuccess(true).build()
        );
        responseObserver.onCompleted();
    }

    @Override
    public void updateGrade(UpdateGradeRequest request, StreamObserver<UpdateGradeResponse> responseObserver) {
        try {
            EnrollmentEntity enrollment = studentService.updateGrade(
                request.getStudentId(), request.getCourseId(),
                request.getSemester(), request.getGrade()
            );
            responseObserver.onNext(
                UpdateGradeResponse.newBuilder()
                    .setEnrollment(toEnrollmentProto(enrollment))
                    .build()
            );
            responseObserver.onCompleted();
        } catch (IllegalArgumentException e) {
            responseObserver.onError(
                Status.NOT_FOUND.withDescription(e.getMessage()).asRuntimeException()
            );
        }
    }

    private Student toProto(StudentEntity entity) {
        Student.Builder builder = Student.newBuilder()
            .setStudentId(entity.getStudentId())
            .setName(entity.getName())
            .setEmail(entity.getEmail());

        for (EnrollmentEntity e : entity.getEnrollments()) {
            builder.addEnrollments(toEnrollmentProto(e));
        }
        return builder.build();
    }

    private Enrollment toEnrollmentProto(EnrollmentEntity entity) {
        Enrollment.Builder builder = Enrollment.newBuilder()
            .setCourseId(entity.getCourseId())
            .setStatus(entity.getStatus());

        if (entity.getSemester() != null) builder.setSemester(entity.getSemester());
        if (entity.getGrade() != null) builder.setGrade(entity.getGrade());
        return builder.build();
    }
}
