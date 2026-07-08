FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        less \
        nodejs \
        npm \
        openssh-client \
        python3 \
        python-is-python3 \
        ripgrep \
        sudo \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh

RUN npm install -g @kilocode/cli \
    && kilo --version

WORKDIR /workspace
