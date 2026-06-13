# Cook widget image — Vite build → static nginx serve.
#
# Two stages keep the runtime image tiny (just the static bundle behind nginx; no Node at runtime).
# VITE_API_BASE is baked at BUILD time (Vite inlines import.meta.env.* into the bundle), so it is passed
# as a build ARG, not a runtime env var. Override per environment:
#   docker build --build-arg VITE_API_BASE=https://api.example.com -t souschef-widget widget/

# ── Stage 1: build the static bundle ─────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app

# Install deps from the lockfile first (better layer caching) — copy manifests, then the source.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The backend origin the bundle will call; defaults to localhost for a bare build.
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

# ── Stage 2: serve the static files ──────────────────────────────────────────
FROM nginx:1.27-alpine AS serve

# SPA fallback: route all paths to index.html so client-side rendering works on refresh.
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
