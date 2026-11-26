# The web app image (the API's is backend/Dockerfile): the production build served by nginx with the CSP and security
# headers of nginx.conf.template, forwarding /api and /webhooks to the API at API_URL (Cloud Run: set at deploy time;
# Kubernetes: infra/k8s/base/web.yaml; self-hosted: scripts/selfhost.sh release; the demo: docker-compose.yml).
#   build    docker build -t welcome-wave-web frontend    (labels: --build-arg VERSION=v1.2.3 --build-arg REVISION=$(git rev-parse HEAD))
# Two stages: Node builds the app (type-check included, so a type error fails the build), and only the built files reach
# nginx. It runs as nginx's own user (uid 101), never root, and works with a read-only root filesystem given writable
# /etc/nginx/conf.d, /var/cache/nginx, /run and /tmp (infra/k8s/base/web.yaml).
# Base images are pinned by digest, so a rebuild gets exactly what was tested; .github/dependabot.yml proposes updates.
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS build
WORKDIR /web
COPY package*.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
# a .gz copy of each text file over 1 kB, which nginx sends as it is (gzip_static), so nothing is compressed per request
RUN npm run build \
 && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' -o -name '*.json' -o -name '*.txt' \) \
      -size +1k -exec gzip -9 -k -n {} +

# nginx 1.30 (stable) on a supported Alpine; runs as the image's unprivileged nginx user, not root
FROM nginx:1.30-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a
COPY --from=build /web/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
RUN sed -i '/^user /d' /etc/nginx/nginx.conf \
 && chown -R nginx:nginx /var/cache/nginx /etc/nginx/conf.d \
 && touch /run/nginx.pid && chown nginx:nginx /run/nginx.pid
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: the entrypoint puts the container's name servers in NGINX_LOCAL_RESOLVERS for the template.
# WEB_HOSTS: the host names served, as a regular expression (any by default; the self-hosted release narrows it to PUBLIC_URL's).
ENV PORT=8080 NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 WEB_HOSTS=".*"
# uid 101: Kubernetes needs the number (runAsUser) to verify the user is not root
USER nginx
EXPOSE 8080

# Docker (compose, the self-hosted release) marks the container unhealthy when nginx stops answering. Kubernetes and Cloud
# Run use their own probes. 127.0.0.1 is among the host names the self-hosted release allows (WEB_HOSTS).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/health" || exit 1

# Last, so a new version or commit changes only this layer
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Welcome Wave web app" \
      org.opencontainers.image.description="Welcome Wave's web app, served by nginx" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
