FROM golang:1.26-alpine AS builder

WORKDIR /app
COPY services/helperium-go/go.mod services/helperium-go/go.sum ./services/helperium-go/
RUN cd services/helperium-go && go mod download

COPY services/admin-dashboard/go.mod services/admin-dashboard/go.sum ./services/admin-dashboard/
WORKDIR /app/services/admin-dashboard
RUN go mod download

COPY services/helperium-go/ /app/services/helperium-go/
COPY services/admin-dashboard/ .
# The dashboard bundle is a build artifact (gitignored `dist/`) and is built
# outside this image on purpose: pulling node/npm into the image just to compile
# one JS file is worse than building first (see `make build-admin`). Guard the
# silent-breakage case instead: without app.js the embedded index.html loads
# /dist/app.js, the SPA fallback answers it with HTML, and the dashboard dies on
# "Unexpected token '<'" with a green CI.
RUN test -f internal/server/static/dist/app.js || { \
      echo "missing internal/server/static/dist/app.js" >&2; \
      echo "build the frontend first: cd services/admin-dashboard && bash build.sh (or: make build-admin)" >&2; \
      exit 1; \
    }
RUN CGO_ENABLED=0 go build -o /admin-dashboard ./cmd/server/

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
RUN adduser -D -u 1001 app
COPY --from=builder /admin-dashboard /admin-dashboard

USER app
EXPOSE 8085
HEALTHCHECK CMD curl -f http://localhost:8085/health || exit 1
CMD ["/admin-dashboard"]
