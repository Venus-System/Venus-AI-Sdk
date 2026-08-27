FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .

RUN addgroup --system venus && adduser --system --ingroup venus venus
USER venus

CMD ["python", "-c", "import venus_sdk"]
