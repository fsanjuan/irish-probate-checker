# -----------------------------------------------------------------------------
# Stage: test
# Includes dev dependencies and test files. Used by CI and for local testing.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS test

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY src/ ./src/
COPY tests/ ./tests/

CMD ["python", "-m", "pytest", "tests/", "-v"]


# -----------------------------------------------------------------------------
# Stage: app
# Lean production image — no pytest, no test files.
# Mount a local directory to ./output to get generated files out.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

CMD ["python", "src/scrape_rip.py", "--help"]
