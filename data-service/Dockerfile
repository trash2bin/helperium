# Data-service: Go-сервис доступа к БД
# Two-stage: golang:1.22-alpine → scratch (минимальный образ, ~15 МБ)
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /build

# Кешируем зависимости
COPY data-service/go.mod data-service/go.sum ./
RUN go mod download

# Копируем исходники и собираем
COPY data-service/ ./
COPY specs/schemas/ /specs/schemas/

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /data-service ./cmd/server/

# ── Runtime ──
FROM scratch

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /data-service /data-service

EXPOSE 8084

ENTRYPOINT ["/data-service"]
