FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock.txt ./requirements.lock.txt
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.lock.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# The API and workers need no writable application directory. Tesseract uses
# the system temporary directory. Do not run untrusted document parsing as root.
RUN groupadd --system --gid 10001 vericlaim \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin vericlaim
USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
