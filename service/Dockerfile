FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# hyplan is mounted as a volume and installed at startup
COPY app.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8100

ENTRYPOINT ["./entrypoint.sh"]
