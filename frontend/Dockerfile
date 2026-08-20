FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Vite bakes VITE_* vars into the bundle at build time, not read at
# container runtime — passed as build args from docker-compose.yml.
ARG VITE_EVENT_SERVICE_URL
ARG VITE_SEARCH_SERVICE_URL
ARG VITE_BOOKING_SERVICE_URL
ARG VITE_KEYCLOAK_ISSUER
ENV VITE_EVENT_SERVICE_URL=${VITE_EVENT_SERVICE_URL}
ENV VITE_SEARCH_SERVICE_URL=${VITE_SEARCH_SERVICE_URL}
ENV VITE_BOOKING_SERVICE_URL=${VITE_BOOKING_SERVICE_URL}
ENV VITE_KEYCLOAK_ISSUER=${VITE_KEYCLOAK_ISSUER}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html/app
COPY nginx.conf /etc/nginx/conf.d/default.conf
