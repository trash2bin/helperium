FROM golang:1.26-alpine AS builder

WORKDIR /app
COPY services/helperium-go/go.mod services/helperium-go/go.sum ./services/helperium-go/
RUN cd services/helperium-go && go mod download

COPY services/admin-dashboard/go.mod services/admin-dashboard/go.sum ./services/admin-dashboard/
WORKDIR /app/services/admin-dashboard
RUN go mod download

COPY services/helperium-go/ /app/services/helperium-go/
COPY services/admin-dashboard/ ./services/admin-dashboard/
RUN CGO_ENABLED=0 go build -o /admin-dashboard ./cmd/server/

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
RUN adduser -D -u 1001 app
COPY --from=builder /admin-dashboard /admin-dashboard

USER app
EXPOSE 8085
HEALTHCHECK CMD curl -f http://localhost:8085/health || exit 1
CMD ["/admin-dashboard"]
