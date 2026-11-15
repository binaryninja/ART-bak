# syntax=docker/dockerfile:1
#
# Runtime image (no compilation inside): install common tools and copy the **precompiled Linux single binary**.
# The CI binaries job cross-compiles the binary (pure Go, no QEMU) into
# dist/<TARGETARCH>/artex in the build context. For multi-architecture builds, arm64 only emulates the apt layer,
# not Next/Go compilation, making builds much faster.
#
# For a manual local image build, prepare the binary first:
#   cd web && npm run build:static && cd ..
#   cp -r web/out server/webui/dist
#   CGO_ENABLED=0 GOARCH=amd64 go build -tags embedui -o dist/amd64/artex ./cmd/artex
#   docker build -t artex:local .
FROM python:3.12-slim-bookworm
ARG TARGETARCH
# Common tools: ripgrep / curl / vim, plus common reconnaissance utilities (adjust as needed).
# Install Node 20.x from NodeSource: bookworm ships Node 18, while Playwright requires >=20.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ripgrep curl wget vim git jq unzip \
      dnsutils iputils-ping netcat-openbsd inetutils-telnet whois nmap \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
# Preinstall Playwright MCP and CLI globally to avoid npx downloads at runtime.
# @playwright/mcp: run the browser MCP with `npx @playwright/mcp` (globally installed; no -y/@latest needed).
# @playwright/cli: provides playwright-cli; verify the executable with --help after installation.
# Also install playwright for browser management, then use --with-deps to preinstall Chromium and its system dependencies,
# so MCP/CLI can run immediately in the container without downloading a browser.
RUN npm install -g @playwright/mcp@latest @playwright/cli@latest playwright@latest \
    && playwright-cli --help \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Precompiled binary for the target architecture (dist/amd64/artex or dist/arm64/artex)
COPY dist/${TARGETARCH}/artex /app/artex
# Supervisor launcher: restart after process exit according to the exit code; the in-app updater relies on this to activate the replacement.
# It also forwards SIGTERM to artex: docker stop sends the signal only to PID 1.
# Without forwarding, artex cannot shut down gracefully and is forcibly killed by SIGKILL after 10 seconds.
COPY start.sh /app/start.sh
RUN chmod +x /app/artex /app/start.sh
COPY skills/ /app/skills/
# data/ persistence mount (SQLite + jwt.key)
VOLUME ["/app/data"]
EXPOSE 8787 8788
ENTRYPOINT ["/app/start.sh"]
CMD ["-addr", ":8787", "-proxy", ":8788"]
