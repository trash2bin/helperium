# Data-service: Go-сервис доступа к БД
# Two-stage: golang:1.24-alpine → gcr.io/distroless/static-debian12:nonroot
FROM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /build

# Кешируем зависимости
COPY services/helperium-go/go.mod services/helperium-go/go.sum /build/services/helperium-go/
COPY services/data-service/go.mod services/data-service/go.sum /build/services/data-service/
RUN cd /build/services/data-service && go mod download

# Копируем исходники и собираем
COPY services/helperium-go/ /build/services/helperium-go/
COPY services/data-service/ /build/services/data-service/
RUN cd /build/services/data-service && CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/data-service ./cmd/server/

# ── Runtime ──
FROM alpine:3.21

RUN apk add --no-cache ca-certificates curl
RUN adduser -D -u 1001 app

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /app/data-service /app/data-service

EXPOSE 8084

USER app

HEALTHCHECK CMD curl -f http://localhost:8084/health || exit 1

ENTRYPOINT ["/app/data-service"]
