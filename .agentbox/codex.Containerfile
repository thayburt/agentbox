FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CODEX_NON_INTERACTIVE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        less \
        openssh-client \
        python3 \
        ripgrep \
        sudo \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/codex-install \
    && curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_HOME=/opt/codex-install CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR=/usr/local/bin sh

ENV CODEX_HOME=/codex-home

WORKDIR /workspace
