FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system aurinara-platform \
    && useradd --system --gid aurinara-platform --create-home aurinara-platform

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
RUN pip install --no-cache-dir .

USER aurinara-platform
CMD ["uvicorn", "aurinara_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]
