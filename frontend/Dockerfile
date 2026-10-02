FROM node:22-alpine
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 3000
# Vite defaults to 5173; docker-compose.yml publishes 3000, so the port is set
# explicitly here rather than left to differ from the mapping.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
