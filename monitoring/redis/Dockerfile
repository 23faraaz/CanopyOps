FROM redis@sha256:2f66de68111e64c3927859f158f8e666066dae2b97d9f82770a6f37a5aa1c335
USER 0:0
# CVE-2026-124709: patch OpenSSL within the upstream Alpine 3.22 branch.
RUN apk add --no-cache --upgrade 'libcrypto3>=3.5.9-r2' 'libssl3>=3.5.9-r2'
USER 10001:10001
