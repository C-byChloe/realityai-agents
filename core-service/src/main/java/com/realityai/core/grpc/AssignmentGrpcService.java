package com.realityai.core.grpc;

import com.realityai.core.entity.AssignmentEntity;
import com.realityai.core.service.AssignmentService;
import com.realityai.proto.Assignment;
import com.realityai.proto.AssignmentServiceGrpc;
import com.realityai.proto.CreateAssignmentRequest;
import com.realityai.proto.CreateAssignmentResponse;
import com.realityai.proto.GetAssignmentRequest;
import com.realityai.proto.GetAssignmentResponse;
import com.realityai.proto.ListAssignmentsRequest;
import com.realityai.proto.ListAssignmentsResponse;
import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import java.util.List;

public class AssignmentGrpcService extends AssignmentServiceGrpc.AssignmentServiceImplBase {

    private final AssignmentService assignmentService;

    public AssignmentGrpcService(AssignmentService assignmentService) {
        this.assignmentService = assignmentService;
    }

    @Override
    public void getAssignment(GetAssignmentRequest request, StreamObserver<GetAssignmentResponse> responseObserver) {
        assignmentService.getAssignment(request.getAssignmentId())
            .ifPresentOrElse(
                entity -> {
                    responseObserver.onNext(
                        GetAssignmentResponse.newBuilder()
                            .setAssignment(toProto(entity))
                            .build()
                    );
                    responseObserver.onCompleted();
                },
                () -> responseObserver.onError(
                    Status.NOT_FOUND
                        .withDescription("Assignment not found: " + request.getAssignmentId())
                        .asRuntimeException()
                )
            );
    }

    @Override
    public void listAssignments(ListAssignmentsRequest request, StreamObserver<ListAssignmentsResponse> responseObserver) {
        List<AssignmentEntity> assignments = assignmentService.listAssignments(request.getCourseId());
        ListAssignmentsResponse.Builder builder = ListAssignmentsResponse.newBuilder();
        assignments.forEach(entity -> builder.addAssignments(toProto(entity)));
        responseObserver.onNext(builder.build());
        responseObserver.onCompleted();
    }

    @Override
    public void createAssignment(CreateAssignmentRequest request, StreamObserver<CreateAssignmentResponse> responseObserver) {
        Assignment proto = request.getAssignment();
        if (proto.getAssignmentId().isEmpty() || proto.getCourseId().isEmpty()) {
            responseObserver.onError(
                Status.INVALID_ARGUMENT
                    .withDescription("assignment_id and course_id are required")
                    .asRuntimeException()
            );
            return;
        }

        AssignmentEntity entity = fromProto(proto);
        AssignmentEntity saved = assignmentService.createAssignment(entity);
        responseObserver.onNext(
            CreateAssignmentResponse.newBuilder()
                .setAssignment(toProto(saved))
                .build()
        );
        responseObserver.onCompleted();
    }

    private Assignment toProto(AssignmentEntity entity) {
        Assignment.Builder builder = Assignment.newBuilder()
            .setAssignmentId(entity.getAssignmentId())
            .setCourseId(entity.getCourseId())
            .setTitle(entity.getTitle())
            .setMaxPoints(entity.getMaxPoints());

        if (entity.getDescription() != null) builder.setDescription(entity.getDescription());
        if (entity.getDueDate() != null) builder.setDueDate(entity.getDueDate());
        return builder.build();
    }

    private AssignmentEntity fromProto(Assignment proto) {
        AssignmentEntity entity = new AssignmentEntity(
            proto.getAssignmentId(), proto.getCourseId(), proto.getTitle()
        );
        entity.setDescription(proto.getDescription());
        entity.setDueDate(proto.getDueDate());
        entity.setMaxPoints(proto.getMaxPoints());
        return entity;
    }
}
