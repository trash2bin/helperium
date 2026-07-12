# Data-service: Go-сервис доступа к БД
# Two-stage: golang:1.24-alpine → gcr.io/distroless/static-debian12:nonroot
FROM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /build

# Кешируем зависимости
COPY helperium-go/go.mod helperium-go/go.sum /build/helperium-go/
COPY data-service/go.mod data-service/go.sum /build/data-service/
RUN cd /build/data-service && go mod download

# Копируем исходники и собираем
COPY helperium-go/ /build/helperium-go/
COPY data-service/ /build/data-service/
RUN cd /build/data-service && CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /data-service ./cmd/server/

# ── Runtime ──
FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /data-service /data-service

EXPOSE 8084

USER nonroot:nonroot

# distroless не содержит curl/wget — HEALTHCHECK невозможен без утилит.
# Для запуска вне docker-compose используйте docker-compose healthcheck (через curl с host).
# data-service имеет /health endpoint на порту 8084.
HEALTHCHECK NONE

ENTRYPOINT ["/data-service"]
