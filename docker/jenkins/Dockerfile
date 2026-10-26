# Jenkins with Python + pytest so test pipelines can run on the built-in node.
FROM jenkins/jenkins:lts-jdk17

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-pytest \
    && rm -rf /var/lib/apt/lists/*
USER jenkins
