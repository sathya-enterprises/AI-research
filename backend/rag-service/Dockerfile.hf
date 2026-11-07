FROM python:3.11-slim

# Install system dependencies for sentence-transformers and pgvector-backed RAG
RUN apt-get update && apt-get install -y \
    build-essential \
    libgfortran5 \
    && rm -rf /var/lib/apt/lists/*

# Set up user. Hugging Face Spaces requires running as non-root user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Copy requirements and install
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download model to user cache so it doesn't download on every boot
RUN python -c "import os; os.environ['HF_HOME']='/home/user/.cache/huggingface'; from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer(os.getenv('EMBED_MODEL','all-MiniLM-L6-v2')); CrossEncoder(os.getenv('HF_RERANK_MODEL','cross-encoder/ms-marco-MiniLM-L6-v2')) if os.getenv('HF_RERANK_ENABLED','1').lower() not in {'0','false','no','off'} else None"

# Copy the rest of the application files
COPY --chown=user . .

# Hugging Face Spaces mandate that the app listens on port 7860
ENV RAG_PORT=7860 RAG_HOST=0.0.0.0
EXPOSE 7860

CMD ["python", "main.py"]
