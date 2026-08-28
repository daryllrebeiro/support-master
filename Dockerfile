# Production build for SupportMaster
FROM python:3.11-slim

WORKDIR /app

# Prevent python from writing pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies into standard system site-packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy repository source files
COPY . .

# Create durable sqlite data folder and set permissions for non-root user
RUN mkdir -p /app/data && \
    useradd -u 8888 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8001

ENV PORT=8001
ENV HOST=0.0.0.0
ENV SUPPORTMASTER_RUN_DB=/app/data/runs.db

# Start SupportMaster web server
CMD ["python", "-m", "supportmaster.web"]
