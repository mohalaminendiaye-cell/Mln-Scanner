FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
# Sécurité : ne pas tourner en root. L'image node:alpine fournit déjà un
# utilisateur non-root standard ("node").
RUN chown -R node:node /app
USER node
EXPOSE 5173
CMD ["serve", "-s", "dist", "-l", "5173"]
