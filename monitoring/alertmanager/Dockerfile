# Rebuild the pinned upstream release with the fixed HTML parser dependency.
FROM golang:1.27-alpine@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS builder
WORKDIR /build
# GOSUMDB verifies the immutable release module; explicitly check its expected sum.
RUN go mod download -json github.com/prometheus/alertmanager@v0.35.0 > /tmp/source.json \
    && grep -F 'h1:nbXMjr+P2JaoFd7aHNDxKifEwTPN+TWxM0gNPonuH5w=' /tmp/source.json \
    && cp -a /go/pkg/mod/github.com/prometheus/alertmanager@v0.35.0/. /build/ \
    && chmod u+w go.mod go.sum
COPY go.mod go.sum ./
RUN go mod download && go mod verify
# Official release UI assets are required by go:embed; never substitute placeholders.
ADD --checksum=sha256:c6af197ce7dbd85be0ac62b50e031eb17945385f782d12039d37f71b752b63db https://github.com/prometheus/alertmanager/releases/download/v0.35.0/alertmanager-web-ui-0.35.0.tar.gz /tmp/ui.tar.gz
RUN tar xzf /tmp/ui.tar.gz -C ui/app \
    && CGO_ENABLED=0 go test -mod=readonly -p 2 ./notify/webhook/... ./config/... \
    && CGO_ENABLED=0 go build -mod=readonly -p 2 -trimpath -ldflags '-s -w -X github.com/prometheus/common/version.Version=0.35.0-local-net0.62.0' -o /out/alertmanager ./cmd/alertmanager \
    && CGO_ENABLED=0 go build -mod=readonly -p 2 -trimpath -ldflags '-s -w -X github.com/prometheus/common/version.Version=0.35.0-local-net0.62.0' -o /out/amtool ./cmd/amtool
FROM prom/alertmanager:v0.35.0@sha256:e638797febd034838eca8b98cdbd46f951e9bf7f178777aa8669167406a9946c
COPY --from=builder /out/alertmanager /bin/alertmanager
COPY --from=builder /out/amtool /bin/amtool
USER 65534:65534
