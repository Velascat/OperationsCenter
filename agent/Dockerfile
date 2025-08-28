FROM python:3.11-slim

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates bash tini openssh-client \
 && rm -rf /var/lib/apt/lists/*

# python deps
WORKDIR /app
COPY agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# non-root
RUN useradd -m app && mkdir -p /workspace && chown -R app:app /workspace /app
USER app
WORKDIR /app

# copy agent
COPY agent/agent.py /app/agent.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/agent.py"]
