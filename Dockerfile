# Dockerfile
FROM python:3.12-slim

# Define variáveis de ambiente para o Python não bufferizar logs
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Instala dependências do sistema necessárias para GeoDjango (GDAL, GEOS, PROJ)
# "netcat-openbsd" é útil para scripts de wait-for-db
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

# Configura diretório de trabalho
WORKDIR /app

# Instala dependências Python
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copia o projeto
COPY . /app/

# Expose não publica a porta, é apenas documentação
EXPOSE 8000

# Comando padrão (pode ser sobrescrito pelo docker-compose)
# CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]