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
# use the pre-built llama-cpp-python, torch cpu wheel
ENV PIP_EXTRA_INDEX_URL="https://download.pytorch.org/whl/cpu https://abetlen.github.io/llama-cpp-python/whl/cpu"
# avoid downloading unused cuda specific python packages
ENV CUDA_VISIBLE_DEVICES=""
RUN sed -i "s/dynamic = \\[\"version\"\\]/version = \"$VERSION\"/" pyproject.toml && \
    pip install --no-cache-dir .

# Build Web App
FROM node:20-alpine AS web-app
# Set build optimization env vars
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app/src/interface/web
# Install dependencies first (cache layer)
COPY src/interface/web/package.json src/interface/web/yarn.lock ./
RUN yarn install --frozen-lockfile
# Copy source and build
COPY src/interface/web/. ./
RUN yarn build

# Merge the Server and Web App into a Single Image
FROM base
ENV PYTHONPATH=/app/src
WORKDIR /app
COPY --from=server-deps /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=web-app /app/src/interface/web/out ./src/khoj/interface/built
COPY . .
RUN cd src && python3 khoj/manage.py collectstatic --noinput

# Run the Application
# There are more arguments required for the application to run,
# but those should be passed in through the docker-compose.yml file.
ARG PORT=42110
EXPOSE ${PORT}
ENTRYPOINT ["python3", "src/khoj/main.py"]
