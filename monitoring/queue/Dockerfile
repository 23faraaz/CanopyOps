FROM softwaremill/elasticmq-native@sha256:e4580ab9ad1bd5cd37b4ba04911bc5ccc8cd2d9ab4de56ece65acee71c24e05c
USER 0:0
# Stable Alpine 3.22 repositories replace older revisions; require patched floors.
# Keep the base digest/branch fixed and scan the resolved image before use.
RUN apk add --no-cache --upgrade \
    'libcrypto3>=3.5.9-r2' 'libssl3>=3.5.9-r2' \
    'musl>=1.2.5-r13' 'musl-utils>=1.2.5-r13' 'zlib>=1.4-r0'
USER 10001:10001
