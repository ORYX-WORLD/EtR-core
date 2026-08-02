FROM node:20-alpine

WORKDIR /app

COPY package.json novnc-entry.mjs ./
# npm prune --omit=dev retirait à tort @firebase/app, dépendance runtime chargée
# par firebase-admin/database-compat, puis le conteneur quittait avec le code 1.
# Le seul outil de développement est esbuild ; le conserver dans l'image est
# préférable à une passerelle impossible à démarrer. Le smoke test vérifie
# désormais explicitement les modules Firebase après construction.
RUN npm install --no-audit --no-fund
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
