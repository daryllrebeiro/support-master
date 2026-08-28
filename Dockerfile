# Multi-stage production build for SupportMaster
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production runner image
FROM python:3.11-slim AS runner

WORKDIR /app

# Copy installed site-packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY . .

# Run as non-root user for security compliance
RUN useradd -u 8888 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

ENV PORT=8001
ENV SUPPORTMASTER_RUN_DB=/app/data/runs.db

# Command to execute the web server
CMD ["python", "-m", "supportmaster.web"]
