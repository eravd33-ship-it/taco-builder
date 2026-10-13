FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY creator_bot ./creator_bot

# The SQLite database lives here – mount a volume so it survives restarts.
VOLUME /app/data
CMD ["python", "-m", "creator_bot"]
