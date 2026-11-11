FROM node:20-alpine

WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_MIXPOST_URL=http://localhost:3002
ARG NEXT_PUBLIC_OPEN_DISPATCH_URL=http://localhost:8010
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_MIXPOST_URL=${NEXT_PUBLIC_MIXPOST_URL}
ENV NEXT_PUBLIC_OPEN_DISPATCH_URL=${NEXT_PUBLIC_OPEN_DISPATCH_URL}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
# La imagen node:20-alpine ya trae el usuario "node" (uid 1000): se usa como
# usuario de ejecucion, sin crear otro (el AGENTS.md prohibe correr como
# root). chown deja /app (node_modules y el .next construido) en manos de ese
# usuario, y HOME apunta a su home para que npm no intente escribir en
# /root/.npm al arrancar.
RUN chown -R node:node /app
ENV HOME=/home/node
EXPOSE 3000
USER node
CMD ["npm", "run", "start"]
