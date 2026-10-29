# A tiny helper image for the application under test. It is used twice in every application pod, without touching the
# application or its Dockerfile:
#  1. init container: copies the Prometheus JMX agent and the async-profiler library into a shared volume; the JVM
#     loads them through JAVA_TOOL_OPTIONS, which works with any JVM image;
#  2. sidecar `morfeo-tools`: sh, tail, wc and stat next to the log and profile volumes, so that the lab can collect
#     them even from images that have no shell.
FROM busybox:1.36
ARG TARGETARCH=amd64
# Pinned versions and checksums: both agents run inside the application's JVM
ARG JMX_EXPORTER_VERSION=1.5.0
ARG JMX_EXPORTER_SHA256=0315f3f657876302c6205a98d4036ec775dca529c5d0419ca60ee669c688239f
ARG ASYNC_PROFILER_VERSION=4.1
ARG ASYNC_PROFILER_SHA256_AMD64=3b13a38a0063f6970d985a379ddaed91bcf37e239a1ea461d09eacf629f3dde1
ARG ASYNC_PROFILER_SHA256_ARM64=d0cb9c97c380672b625c06e5a3ed578e990f4674c6aae8b5249f584c4c9ac50e
ADD https://github.com/prometheus/jmx_exporter/releases/download/${JMX_EXPORTER_VERSION}/jmx_prometheus_javaagent-${JMX_EXPORTER_VERSION}.jar /agent/jmx.jar
ADD https://github.com/async-profiler/async-profiler/releases/download/v${ASYNC_PROFILER_VERSION}/async-profiler-${ASYNC_PROFILER_VERSION}-linux-x64.tar.gz /tmp/ap-amd64.tgz
ADD https://github.com/async-profiler/async-profiler/releases/download/v${ASYNC_PROFILER_VERSION}/async-profiler-${ASYNC_PROFILER_VERSION}-linux-arm64.tar.gz /tmp/ap-arm64.tgz
COPY config.yaml /agent/config.yaml
RUN echo "${JMX_EXPORTER_SHA256}  /agent/jmx.jar" | sha256sum -c - \
 && echo "${ASYNC_PROFILER_SHA256_AMD64}  /tmp/ap-amd64.tgz" | sha256sum -c - \
 && echo "${ASYNC_PROFILER_SHA256_ARM64}  /tmp/ap-arm64.tgz" | sha256sum -c - \
 && mkdir -p /tmp/ap && tar xzf "/tmp/ap-${TARGETARCH}.tgz" -C /tmp/ap \
 && cp /tmp/ap/*/lib/libasyncProfiler.so /agent/libasyncProfiler.so \
 && rm -rf /tmp/ap /tmp/ap-*.tgz \
 && chmod 0644 /agent/jmx.jar /agent/config.yaml /agent/libasyncProfiler.so
CMD ["sh", "-c", "cp /agent/jmx.jar /agent/config.yaml /agent/libasyncProfiler.so /morfeo-agent/ && echo 'agents ready'"]
