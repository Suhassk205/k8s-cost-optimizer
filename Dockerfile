# Dockerfile
# KubeCost-Gym Docker Image
# Base: pinned Python 3.10 slim for stable Docker builds
# Deployment: HuggingFace Spaces with cpu-basic hardware

FROM python:3.10.18-slim-bullseye

# Set working directory
WORKDIR /app

# Copy build definition first (layer caching)
COPY pyproject.toml .
# Optionally copy lockfile for reproducibility if present
COPY uv.lock* .

# Install dependencies directly from pyproject.toml
# Using --upgrade pip for manifest reliability as requested by SDD Phase 5
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --retries 3 --timeout 60 .

# Copy application code
COPY . .

# Verify critical files exist in root (spec §5)
RUN test -f inference.py  || (echo "ERROR: inference.py not found in root" && exit 1)
RUN test -f app.py        || (echo "ERROR: app.py not found in root" && exit 1)
RUN test -f env.py        || (echo "ERROR: env.py not found in root" && exit 1)
RUN test -f graders.py    || (echo "ERROR: graders.py not found in root" && exit 1)
RUN test -f models.py     || (echo "ERROR: models.py not found in root" && exit 1)
RUN test -f openenv.yaml  || (echo "ERROR: openenv.yaml not found in root" && exit 1)

# Verify traces are present
RUN test -f traces/trace_v1_coldstart.json || (echo "ERROR: trace_v1_coldstart.json missing" && exit 1)
RUN test -f traces/trace_v1_squeeze.json   || (echo "ERROR: trace_v1_squeeze.json missing" && exit 1)
RUN test -f traces/trace_v1_entropy.json   || (echo "ERROR: trace_v1_entropy.json missing" && exit 1)

# Validate openenv.yaml is parseable
RUN python -c "import yaml; yaml.safe_load(open('openenv.yaml'))" || (echo "ERROR: openenv.yaml invalid YAML" && exit 1)

# Expose port (HuggingFace Spaces standard)
EXPOSE 7860

# Use Astral UV runtime to launch the application
ENV PORT=7860
ENV SERVER_NAME=0.0.0.0
CMD ["uv", "run", "python", "server/app.py"]
