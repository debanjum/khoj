# syntax=docker/dockerfile:1
FROM ubuntu:jammy AS base
LABEL homepage="https://khoj.dev"
LABEL repository="https://github.com/khoj-ai/khoj"
LABEL org.opencontainers.image.source="https://github.com/khoj-ai/khoj"
LABEL org.opencontainers.image.description="Your second brain, containerized for personal, local deployment."

# Install System Dependencies
RUN apt update -y && apt -y install \
    python3-pip \
    swig \
    curl \
    # Required by RapidOCR
    libgl1 \
    libglx-mesa0 \
    libglib2.0-0 \
    # Required by llama-cpp-python pre-built wheels. See #1628
    musl-dev && \
    ln -s /usr/lib/x86_64-linux-musl/libc.so /lib/libc.musl-x86_64.so.1 && \
    # Clean up
    apt clean && rm -rf /var/lib/apt/lists/*

# Build Server
FROM base AS server-deps
WORKDIR /app
COPY pyproject.toml .
COPY README.md .
ARG VERSION=0.0.0

# Install dependencies
ENV PIP_EXTRA_INDEX_URL="https://download.pytorch.org/whl/cpu https://abetlen.github.io/llama-cpp-python/whl/cpu"
ENV CUDA_VISIBLE_DEVICES=""

# First install core dependencies
RUN pip install --no-cache-dir pip==24.0 && \
    pip install --no-cache-dir \
    django==5.0.10 \
    fastapi==0.115.6 \
    uvicorn==0.30.6 \
    pydantic==2.10.5 \
    starlette==0.41.3

# Then install the package
RUN sed -i "s/dynamic = \[\"version\"\]/version = \"$VERSION\"/" pyproject.toml && \
    pip install --no-cache-dir .

# Build Web App
FROM node:20-bullseye AS web-app
# Set build optimization env vars
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app/src/interface/web

# Configure yarn for better network resilience
RUN yarn config set network-timeout 300000 && \
    yarn config set network-concurrency 1 && \
    yarn config set retry-number 5

# Install dependencies first (cache layer)
COPY src/interface/web/package.json src/interface/web/yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 300000 --network-concurrency 1

# Copy source and build
COPY src/interface/web/. ./
RUN yarn build

# Merge the Server and Web App into a Single Image
FROM base
ENV PYTHONPATH=/app/src
WORKDIR /app

# Copy Python packages and web build
COPY --from=server-deps /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=web-app /app/src/interface/web/out ./src/khoj/interface/built

# Copy source code
COPY . .

# Collect static files
RUN cd src && python3 -m pip install django==5.0.10 && python3 khoj/manage.py collectstatic --noinput

# Run the Application
ARG PORT=42110
EXPOSE ${PORT}
ENTRYPOINT ["python3", "src/khoj/main.py"]
