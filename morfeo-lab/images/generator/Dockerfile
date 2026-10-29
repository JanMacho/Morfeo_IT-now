# The load generator: Apache JMeter and its plugins plus the live metrics exporter. It runs the .jmx plans of the
# AI team as they are. Versions are pinned and checked (plugins.json): the same plan always runs on the same engine,
# otherwise results of different runs would not be comparable.
#
#   docker build -t morfeo/generator images/generator          (done by `morfeo-lab up`)
#   --build-arg JMETER_PLUGINS=jpgc-casutg,jpgc-tst,...        (generators.jmeter_plugins in config/lab.yaml)
ARG JAVA_IMAGE=eclipse-temurin:21-jre
FROM ${JAVA_IMAGE}
ARG JMETER_PLUGINS=jpgc-casutg,jpgc-tst,jpgc-functions,jpgc-dummy
ENV JMETER_HOME=/opt/apache-jmeter \
    MORFEO_HOME=/opt/morfeo \
    LANG=C.UTF-8 \
    JVM_ARGS="-Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8"
ENV PATH=${JMETER_HOME}/bin:${PATH}

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# JMeter and plugins, downloaded and verified now: a test never downloads anything.
# On a network that inspects HTTPS with its own certificate authority, `up` passes that certificate as the
# build secret `extra_ca` (MORFEO_BUILD_CA); without one nothing changes.
COPY plugins.json install_jmeter.py /opt/morfeo/
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then \
      cp /run/secrets/extra_ca /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates; \
    fi \
 && python3 /opt/morfeo/install_jmeter.py --plugins "${JMETER_PLUGINS}"

COPY jmeter-user.properties ${JMETER_HOME}/bin/user.properties
COPY jtl_exporter.py entrypoint.sh jmeter-report.sh engine-check.sh engine-check.jmx engine-check.csv /opt/morfeo/
# The build fails here if JMeter cannot run a plan with every kind of thread group the lab reads
RUN chmod +x /opt/morfeo/entrypoint.sh /opt/morfeo/jmeter-report.sh /opt/morfeo/engine-check.sh \
 && /opt/morfeo/engine-check.sh

ENTRYPOINT ["/opt/morfeo/entrypoint.sh"]
