FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
  git \
  make \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./

# Install dependencies using uv
RUN /root/.local/bin/uv venv
RUN /root/.local/bin/uv pip install .

# Copy source code
COPY src/ src/
COPY Makefile .

# Set environment variables
ENV PYTHONPATH=/app

# Run the application
ENTRYPOINT ["/root/.local/bin/uv", "run"]
CMD ["python", "./src/main.py", "trade", "-i", "60"]