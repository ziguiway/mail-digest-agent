FROM node:22-bookworm-slim AS web
WORKDIR /web
RUN npm install --global pnpm@11.25.0
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

FROM node:22-bookworm-slim AS lark
RUN apt-get update && apt-get install --yes --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global @larksuite/cli@1.0.97 && lark-cli --version

FROM python:3.11-slim-bookworm AS python-build
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.32
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy uv sync --all-packages --frozen --no-dev --no-install-workspace
COPY mail_agent/ ./mail_agent/
COPY backend/ ./backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy uv sync --all-packages --frozen --no-dev

FROM python:3.11-slim-bookworm AS runtime
RUN apt-get update && apt-get install --yes --no-install-recommends \
    ca-certificates libatomic1 libstdc++6 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid assistant --home-dir /var/lib/mail-assistant/cli-home assistant \
    && install -d -o assistant -g assistant -m 700 /var/lib/mail-assistant
WORKDIR /app
COPY --from=python-build /app/.venv/ /app/.venv/
COPY --from=python-build /app/mail_agent/ /app/mail_agent/
COPY --from=python-build /app/backend/ /app/backend/
COPY --from=web /web/dist/ /app/apps/web/dist/
COPY --from=lark /usr/local/bin/node /usr/local/bin/node
COPY --from=lark /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
RUN ln -s /usr/local/lib/node_modules/@larksuite/cli/scripts/run.js /usr/local/bin/lark-cli
COPY deploy/health.py deploy/check_mail_connection.py /app/deploy/
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/mail-assistant/cli-home \
    MAIL_ASSISTANT_DATA_DIR=/var/lib/mail-assistant \
    MAIL_ASSISTANT_WEB_DIST=/app/apps/web/dist \
    TZ=Asia/Shanghai
USER assistant
EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/app/.venv/bin/mail-assistant"]
CMD ["--host", "0.0.0.0", "api"]
