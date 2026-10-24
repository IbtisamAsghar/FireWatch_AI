# Historical API with portable public model/evidence; database stays on Neon.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src \
    APP_ENV=production ACTIVE_SCORER=registry \
    MODEL_DIR=/app/deploy/render/model SOURCE_ARTIFACT_DIR=/app/deploy/render/assets \
    DB_POOL_SIZE=2 DB_MAX_OVERFLOW=1 DB_CONNECT_TIMEOUT_SECONDS=30 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip check \
    && groupadd --gid 10001 firewatch \
    && useradd --uid 10001 --gid firewatch --no-create-home firewatch
COPY src ./src
COPY configs ./configs
COPY deploy/render ./deploy/render
COPY scripts/render_assets.py ./scripts/render_assets.py
RUN python scripts/render_assets.py verify
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "firewatch.runtime"]
CMD ["uvicorn", "firewatch.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
