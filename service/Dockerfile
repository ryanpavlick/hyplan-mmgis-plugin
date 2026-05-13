FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git curl libeccodes-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the service package (app.py, __init__.py, state.py, errors.py,
# schemas.py, and routers/) into /app/service so `uvicorn
# service.app:app` resolves the package the same way it does in dev.
# hyplan is mounted as a volume and installed at startup by
# entrypoint.sh.
COPY __init__.py app.py state.py errors.py schemas.py /app/service/
COPY routers /app/service/routers
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8100

ENTRYPOINT ["./entrypoint.sh"]
