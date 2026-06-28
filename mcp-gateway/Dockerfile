# Multi-stage build for mcp-gateway
FROM golang:1.24-alpine AS builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /build/mcp-gateway ./cmd/main.go

FROM alpine:3.21
RUN apk add --no-cache ca-certificates
RUN adduser -D -u 1001 app
USER app
WORKDIR /app
COPY --from=builder /build/mcp-gateway /app/mcp-gateway

EXPOSE 8083
ENTRYPOINT ["/app/mcp-gateway"]
CMD ["--config", "/app/config.json"]
