# Dockerfile
FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies required for GeoDjango (GDAL, GEOS, PROJ)
# "netcat-openbsd" is useful for wait-for-db scripts
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       binutils \
       libproj-dev \
       gdal-bin \
       libgdal-dev \
       python3-gdal \
       gcc \
       netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project files
COPY . /app/

# Expose is documentation only (does not publish the port)
EXPOSE 8000

# Default command (can be overridden by docker-compose)
# CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
