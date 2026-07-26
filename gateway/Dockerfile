FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json novnc-entry.mjs ./
RUN npm ci --ignore-scripts
RUN mkdir -p public && npm run build:novnc && npm prune --omit=dev
COPY server.mjs firebase-token-verifier.mjs ./
USER node
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
