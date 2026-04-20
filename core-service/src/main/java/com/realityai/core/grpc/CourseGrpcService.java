package com.realityai.core.grpc;

import com.realityai.core.entity.CourseEntity;
import com.realityai.core.service.CourseService;
import com.realityai.proto.Course;
import com.realityai.proto.CourseServiceGrpc;
import com.realityai.proto.GetCourseRequest;
import com.realityai.proto.GetCourseResponse;
import com.realityai.proto.ListCoursesRequest;
import com.realityai.proto.ListCoursesResponse;
import com.realityai.proto.UpdateCourseRequest;
import com.realityai.proto.UpdateCourseResponse;
import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import java.util.Arrays;
import java.util.List;

public class CourseGrpcService extends CourseServiceGrpc.CourseServiceImplBase {

    private final CourseService courseService;

    public CourseGrpcService(CourseService courseService) {
        this.courseService = courseService;
    }

    @Override
    public void getCourse(GetCourseRequest request, StreamObserver<GetCourseResponse> responseObserver) {
        courseService.getCourse(request.getCourseId())
            .ifPresentOrElse(
                entity -> {
                    responseObserver.onNext(
                        GetCourseResponse.newBuilder()
                            .setCourse(toProto(entity))
                            .build()
                    );
                    responseObserver.onCompleted();
                },
                () -> responseObserver.onError(
                    Status.NOT_FOUND
                        .withDescription("Course not found: " + request.getCourseId())
                        .asRuntimeException()
                )
            );
    }

    @Override
    public void listCourses(ListCoursesRequest request, StreamObserver<ListCoursesResponse> responseObserver) {
        List<CourseEntity> courses = courseService.listCourses(request.getSemester());
        ListCoursesResponse.Builder builder = ListCoursesResponse.newBuilder();
        courses.forEach(entity -> builder.addCourses(toProto(entity)));
        responseObserver.onNext(builder.build());
        responseObserver.onCompleted();
    }

    @Override
    public void updateCourse(UpdateCourseRequest request, StreamObserver<UpdateCourseResponse> responseObserver) {
        Course proto = request.getCourse();
        if (proto.getCourseId().isEmpty()) {
            responseObserver.onError(
                Status.INVALID_ARGUMENT
                    .withDescription("course_id is required")
                    .asRuntimeException()
            );
            return;
        }

        CourseEntity entity = fromProto(proto);
        CourseEntity saved = courseService.updateCourse(entity);
        responseObserver.onNext(
            UpdateCourseResponse.newBuilder()
                .setCourse(toProto(saved))
                .build()
        );
        responseObserver.onCompleted();
    }

    private Course toProto(CourseEntity entity) {
        Course.Builder builder = Course.newBuilder()
            .setCourseId(entity.getCourseId())
            .setName(entity.getName())
            .setCredits(entity.getCredits());

        if (entity.getDescription() != null) builder.setDescription(entity.getDescription());
        if (entity.getInstructor() != null) builder.setInstructor(entity.getInstructor());
        if (entity.getSchedule() != null) builder.setSchedule(entity.getSchedule());
        if (entity.getLocation() != null) builder.setLocation(entity.getLocation());
        if (entity.getSemester() != null) builder.setSemester(entity.getSemester());
        if (entity.getPrerequisites() != null && !entity.getPrerequisites().isEmpty()) {
            builder.addAllPrerequisites(Arrays.asList(entity.getPrerequisites().split(",")));
        }

        return builder.build();
    }

    private CourseEntity fromProto(Course proto) {
        CourseEntity entity = new CourseEntity(proto.getCourseId(), proto.getName(), proto.getInstructor());
        entity.setDescription(proto.getDescription());
        entity.setSchedule(proto.getSchedule());
        entity.setLocation(proto.getLocation());
        entity.setCredits(proto.getCredits());
        entity.setSemester(proto.getSemester());
        if (!proto.getPrerequisitesList().isEmpty()) {
            entity.setPrerequisites(String.join(",", proto.getPrerequisitesList()));
        }
        return entity;
    }
}
