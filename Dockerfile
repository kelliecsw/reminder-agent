FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PATH="/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /

RUN apt-get update \
    && apt-get install --yes --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md /
RUN uv sync --frozen --no-dev --no-install-project

COPY reminder_agent/ /reminder_agent/
RUN uv sync --frozen --no-dev

CMD ["/.venv/bin/python", "-m", "reminder_agent.app"]
