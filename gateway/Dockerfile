FROM node:20-alpine
WORKDIR /app
COPY package.json ./
RUN npm install
RUN mkdir -p public && npm run build:novnc && npm prune --omit=dev
COPY server.mjs firebase-token-verifier.mjs ./
USER node
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
