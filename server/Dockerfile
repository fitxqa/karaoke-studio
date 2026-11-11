FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-ui-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
# CPU build of PyTorch (smaller). For an NVIDIA GPU server use the CUDA wheels instead.
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV KARAOKE_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
