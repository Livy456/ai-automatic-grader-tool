FROM python:3.11-slim
 
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
 
ENV PYTHONUNBUFFERED=1
 
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
 
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]