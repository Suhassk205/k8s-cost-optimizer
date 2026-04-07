# Dockerfile
# KubeCost-Gym Docker Image
# Optimized for UV runtime and stable Python 3.10

# Step 1: Use a stable Python slim image
FROM python:3.10-slim-bookworm

# Step 2: Install UV binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation and frozen locks
ENV UV_COMPILE_BYTECODE=1
ENV UV_FROZEN=1

# Step 3: Copy build definition to cache dependencies
COPY pyproject.toml uv.lock ./

# Step 4: Install dependencies without installing the project itself
# This layer is cached until pyproject.toml or uv.lock changes
RUN uv sync --no-install-project --no-dev

# Step 5: Copy application code
COPY . .

# Step 6: Install the project itself (metadata + scripts)
RUN uv sync --no-dev

# Step 7: Verify critical files exist in root (spec §5)
RUN test -f inference.py  || (echo "ERROR: inference.py not found" && exit 1)
RUN test -f app.py        || (echo "ERROR: app.py not found" && exit 1)
RUN test -f env.py        || (echo "ERROR: env.py not found" && exit 1)
RUN test -f graders.py    || (echo "ERROR: graders.py not found" && exit 1)
RUN test -f models.py     || (echo "ERROR: models.py not found" && exit 1)
RUN test -f openenv.yaml  || (echo "ERROR: openenv.yaml not found" && exit 1)

# Verify traces are present
RUN test -f traces/trace_v1_coldstart.json || (echo "ERROR: traces/trace_v1_coldstart.json missing" && exit 1)
RUN test -f traces/trace_v1_squeeze.json   || (echo "ERROR: traces/trace_v1_squeeze.json missing" && exit 1)
RUN test -f traces/trace_v1_entropy.json   || (echo "ERROR: traces/trace_v1_entropy.json missing" && exit 1)

# Validate openenv.yaml is parseable
RUN uv run python -c "import yaml; yaml.safe_load(open('openenv.yaml'))" || (echo "ERROR: openenv.yaml invalid YAML" && exit 1)

# Expose port (HuggingFace Spaces standard)
EXPOSE 7860

# Runtime configuration
ENV PORT=7860
ENV SERVER_NAME=0.0.0.0

# Launch application using uv run for correct environment management
CMD ["uv", "run", "python", "server/app.py"]
