FROM python:3.7-alpine

ADD . /app
RUN apk add openjdk11-jdk nodejs-current \
    && cd /app \
    && pip install . \
    && rm -rf /app \
    && sonarlint-cli prefetch

ENTRYPOINT ["/usr/local/bin/sonarlint-cli", "analyse"]