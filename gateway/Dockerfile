FROM node:20-alpine

WORKDIR /app

COPY package.json novnc-entry.mjs ./
RUN npm install --no-audit --no-fund
RUN mkdir -p public \
  && npm run build:novnc \
  && npm prune --omit=dev \
  && node -e "require('@firebase/app'); require('@firebase/database-compat/standalone'); require('firebase-admin')"

# Runtime modules are listed explicitly so a new security-critical component
# cannot be assumed present merely because it exists in the repository.
COPY server.mjs ./
COPY health.mjs ./
COPY firebase-token-verifier.mjs ./
COPY device-authorization.mjs ./
COPY remote-screen-diagnostic.mjs ./
COPY enrollment.mjs ./
COPY enrollment-http.mjs ./
COPY device-bootstrap.mjs ./
COPY firebase-device-session.mjs ./
COPY admin.mjs ./
COPY admin-legacy.mjs ./

USER node
ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
