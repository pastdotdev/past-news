# The newsroom as one container: the reader with its fetch loop, on port 8080.
#
#   docker build -t past-news .
#   docker run --rm -p 8080:8080 -e NEWSROOM_KEY_AI_REGULATION=past_sk_... past-news
#
# The image carries topics.yaml (feed URLs and variable names, no keys); every key is read from
# the environment at runtime. Pass --no-fetch as the command's last argument to serve without
# fetching when a scheduled workflow does the ingestion instead.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.7.0 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# README.md and LICENSE are package metadata; the wheel build reads them.
COPY README.md LICENSE ./
COPY newsroom ./newsroom
COPY topics.yaml ./topics.yaml
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
ENV NEWSROOM_TOPICS=/app/topics.yaml
RUN useradd --create-home newsroom
USER newsroom
EXPOSE 8080
CMD ["newsroom", "serve", "--host", "0.0.0.0", "--port", "8080"]
