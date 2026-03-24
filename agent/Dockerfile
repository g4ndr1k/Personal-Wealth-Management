FROM python:3.12-slim

RUN useradd -m -s /bin/bash agentuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app

RUN mkdir -p /app/data /app/config
RUN chown -R agentuser:agentuser /app

USER agentuser

CMD ["python", "-m", "app.main"]
