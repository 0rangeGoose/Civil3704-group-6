# Multi-stage Dockerfile for flexible language support
# Adjust based on your primary implementation language

# Stage 1: Base image with common tools
FROM python:3.11-slim as base

WORKDIR /app

# Install common system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    make \
    && rm -rf /var/lib/apt/lists/*

# Stage 2: Python-specific setup (default)
FROM base as python-runtime

# Copy dependency files
COPY requirements.txt* ./

# Install Python dependencies if requirements.txt exists
RUN if [ -f requirements.txt ]; then \
    pip install --no-cache-dir -r requirements.txt; \
    fi

# Copy project files
COPY . .

# Create necessary directories
RUN mkdir -p data/gtfs logs

# Expose common ports
EXPOSE 8080 5000

# Default command
CMD ["make", "run"]

# Alternative: Node.js runtime (uncomment if using Node)
# FROM node:18-slim as node-runtime
# WORKDIR /app
# COPY package*.json ./
# RUN npm install
# COPY . .
# EXPOSE 8080
# CMD ["npm", "start"]

# Alternative: Go runtime (uncomment if using Go)
# FROM golang:1.21-alpine as go-runtime
# WORKDIR /app
# COPY go.mod go.sum ./
# RUN go mod download
# COPY . .
# RUN go build -o /app/bin/ar-transit ./src
# CMD ["/app/bin/ar-transit"]
