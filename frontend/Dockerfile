# Stage 1: Build & Test the Angular application
FROM node:24-alpine AS base

WORKDIR /app

# Copy package configuration files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy the rest of the application source code
COPY . .

# --- Test Stage ---
FROM base AS test
RUN apk add --no-cache chromium
ENV CHROME_BIN=/usr/bin/chromium-browser
RUN npx ng test --no-watch --no-progress --browsers=ChromeHeadlessNoSandbox

# --- Build Stage ---
FROM base AS builder
# Build the application for production
RUN npm run build -- --configuration production

# Stage 2: Serve the application with Nginx
FROM nginx:alpine

# Copy the built application from the builder stage
COPY --from=builder /app/dist/crpaas-ui/browser /usr/share/nginx/html

# Copy the Nginx configuration template
COPY nginx.conf /etc/nginx/templates/default.conf.template

# When the container starts, substitute environment variables in the template
# and start Nginx.
CMD ["/bin/sh", "-c", "envsubst '${API_UPSTREAM}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]