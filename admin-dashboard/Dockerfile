FROM golang:1.24-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /admin-dashboard ./cmd/server/

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=builder /admin-dashboard /admin-dashboard

EXPOSE 8085
CMD ["/admin-dashboard"]
