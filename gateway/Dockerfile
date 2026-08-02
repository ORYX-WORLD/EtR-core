FROM node:20-alpine

WORKDIR /app

COPY package.json novnc-entry.mjs ./
RUN npm install --no-audit --no-fund
# Diagnostic: conserver temporairement les dépendances de développement pour
# vérifier si `npm prune --omit=dev` retire un pair requis par firebase-admin.
RUN mkdir -p public && npm run build:novnc

# Runtime modules are listed explicitly so a new security-critical component
# cannot be assumed present merely because it exists in the repository.
COPY server.mjs ./
COPY firebase-token-verifier.mjs ./
COPY enrollment.mjs ./
COPY enrollment-http.mjs ./
COPY device-bootstrap.mjs ./
COPY firebase-device-session.mjs ./
COPY admin.mjs ./

USER node
ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
