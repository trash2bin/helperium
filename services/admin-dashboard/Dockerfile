FROM golang:1.26-alpine AS builder

WORKDIR /app
COPY helperium-go/go.mod helperium-go/go.sum ./helperium-go/
RUN cd helperium-go && go mod download

COPY admin-dashboard/go.mod admin-dashboard/go.sum ./admin-dashboard/
WORKDIR /app/admin-dashboard
RUN go mod download

COPY helperium-go/ /app/helperium-go/
COPY admin-dashboard/ ./
RUN CGO_ENABLED=0 go build -o /admin-dashboard ./cmd/server/

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
RUN adduser -D -u 1001 app
COPY --from=builder /admin-dashboard /admin-dashboard

USER app
EXPOSE 8085
HEALTHCHECK CMD curl -f http://localhost:8085/health || exit 1
CMD ["/admin-dashboard"]
