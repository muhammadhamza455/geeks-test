# WikiPulse Go ingestion service (build context: repository root)
FROM golang:1.27-alpine AS build
WORKDIR /src
# Standard library only (no go.sum / module downloads needed)
COPY ingest-go/ ./
RUN go vet ./... && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ingest .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 ingest
USER ingest
COPY --from=build /out/ingest /usr/local/bin/ingest
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=4s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8090/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/ingest"]
