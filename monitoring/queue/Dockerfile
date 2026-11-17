FROM softwaremill/elasticmq-native@sha256:e4580ab9ad1bd5cd37b4ba04911bc5ccc8cd2d9ab4de56ece65acee71c24e05c
USER 0:0
# Available security fixes in the same upstream Alpine 3.22 repository.
RUN apk add --no-cache libcrypto3=3.5.9-r0 libssl3=3.5.9-r0 musl=1.2.5-r13 musl-utils=1.2.5-r13 zlib=1.4-r0
USER 10001:10001
