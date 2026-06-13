FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROJECT_KNOWLEDGE_CONFIG=/workspace/project.yaml

WORKDIR /app

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
COPY spikes ./spikes

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip \
    && python -m pip install '.[codegraph]'

EXPOSE 8000

ENTRYPOINT ["project-knowledge"]
CMD ["start", "--transport", "stdio"]
