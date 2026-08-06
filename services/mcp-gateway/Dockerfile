FROM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /build

COPY services/helperium-go/go.mod services/helperium-go/go.sum /build/services/helperium-go/
COPY services/mcp-gateway/go.mod services/mcp-gateway/go.sum /build/services/mcp-gateway/
RUN cd /build/services/mcp-gateway && go mod download

COPY services/helperium-go/ /build/services/helperium-go/
COPY services/mcp-gateway/ /build/services/mcp-gateway/
RUN cd /build/services/mcp-gateway && CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/mcp-gateway ./cmd/

# ── Runtime ──
FROM alpine:3.21

RUN apk add --no-cache ca-certificates
RUN adduser -D -u 1001 app

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /app/mcp-gateway /app/mcp-gateway

EXPOSE 8083

USER app

ENTRYPOINT ["/app/mcp-gateway"]
