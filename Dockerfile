FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ERPNEXT_MCP_HOST=0.0.0.0 \
    ERPNEXT_MCP_PORT=9000

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install .

USER app

EXPOSE 9000

CMD ["erpnext-mcp"]
