FROM node:20-alpine

WORKDIR /app

COPY package.json novnc-entry.mjs ./
RUN npm install --no-audit --no-fund
RUN mkdir -p public && npm run build:novnc && npm prune --omit=dev

# Runtime modules are listed explicitly so a new security-critical component
# cannot be assumed present merely because it exists in the repository.
COPY server.mjs ./
COPY firebase-token-verifier.mjs ./
COPY enrollment.mjs ./
COPY enrollment-http.mjs ./
COPY device-bootstrap.mjs ./
COPY device-session.mjs ./
COPY session-config.mjs ./

USER node
ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
