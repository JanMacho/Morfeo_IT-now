# Runtime for any executable Java JAR (fat JAR, Spring Boot, Javalin, Quarkus uber-jar...).
# Built by `morfeo-lab deploy` with --build-arg JAVA_VERSION=<n> and the JAR as `app.jar` in the build context.
# Nothing specific to an application lives here.
#
# A glibc-based JRE (Ubuntu), not Alpine: some JARs ship native libraries (Netty epoll, Snappy, RocksDB, gRPC...)
# built for glibc that would crash on musl, and the profiler needs glibc too.
ARG JAVA_VERSION=21
FROM eclipse-temurin:${JAVA_VERSION}-jre

WORKDIR /app
COPY app.jar /opt/app/app.jar
RUN mkdir -p /logs && chown -R 10001:0 /app /logs && chmod -R g+rwX /app /logs

# Unprivileged user without a passwd entry: HOME must point somewhere writable
USER 10001
ENV HOME=/tmp \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0" \
    APP_ARGS=""
# JAVA_OPTS comes from the application file (runtime.java_opts). The metrics agent and the profiler are injected at
# deploy time through JAVA_TOOL_OPTIONS, so the same image works with or without them.
# `exec` makes java the main process: SIGTERM reaches the application (a graceful shutdown flushes its logs).
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -Djava.security.egd=file:/dev/./urandom -jar /opt/app/app.jar $APP_ARGS"]
