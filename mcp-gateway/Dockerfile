# Multi-stage build for mcp-gateway
FROM golang:1.24-alpine AS builder

WORKDIR /build

# Кешируем зависимости
COPY agent-tutor-go/go.mod agent-tutor-go/go.sum /build/agent-tutor-go/
COPY mcp-gateway/go.mod mcp-gateway/go.sum /build/mcp-gateway/
RUN cd /build/mcp-gateway && go mod download

# Копируем исходники и собираем
COPY agent-tutor-go/ /build/agent-tutor-go/
COPY mcp-gateway/ /build/mcp-gateway/
RUN cd /build/mcp-gateway && CGO_ENABLED=0 go build -o /build/mcp-gateway ./cmd/main.go

FROM alpine:3.21
RUN apk add --no-cache ca-certificates curl
RUN adduser -D -u 1001 app
USER app
WORKDIR /app
COPY --from=builder /build/mcp-gateway /app/mcp-gateway

EXPOSE 8083
HEALTHCHECK CMD curl -f http://localhost:8083/health || exit 1
ENTRYPOINT ["/app/mcp-gateway"]
