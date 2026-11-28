FROM python:3.11-slim-bookworm
ENV SDL_VIDEODRIVER=dummy
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 libatomic1 libgl1 libx11-6 libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /framework
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY examples/dungeon-navigation/Build/Linux /app
RUN chmod +x /app/DungeonTrials.x86_64
ENTRYPOINT ["qa-run-trial", "--", "/app/DungeonTrials.x86_64", "-batchmode", "-nographics", "-logFile", "/qa/output/unity.log"]
