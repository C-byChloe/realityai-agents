package com.realityai.core.grpc;

import com.realityai.core.service.AssignmentService;
import com.realityai.core.service.CourseService;
import com.realityai.core.service.StudentService;
import io.grpc.Server;
import io.grpc.ServerBuilder;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;

@Configuration
public class GrpcServerConfig {

    @Value("${grpc.server.port:9090}")
    private int grpcPort;

    private Server server;

    private final CourseService courseService;
    private final StudentService studentService;
    private final AssignmentService assignmentService;

    public GrpcServerConfig(
        CourseService courseService,
        StudentService studentService,
        AssignmentService assignmentService
    ) {
        this.courseService = courseService;
        this.studentService = studentService;
        this.assignmentService = assignmentService;
    }

    @PostConstruct
    public void start() throws Exception {
        server = ServerBuilder.forPort(grpcPort)
            .addService(new CourseGrpcService(courseService))
            .addService(new StudentGrpcService(studentService))
            .addService(new AssignmentGrpcService(assignmentService))
            .build()
            .start();
    }

    @PreDestroy
    public void stop() {
        if (server != null) {
            server.shutdown();
        }
    }
}
