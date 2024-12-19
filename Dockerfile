# Use Selenium standalone Chrome as the base image
FROM selenium/standalone-chrome:latest

# Set environment variables to avoid prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install Python, pip, and other dependencies
USER root
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set Python alias for convenience
RUN ln -s /usr/bin/python3 /usr/bin/python

# Copy application files into the container
WORKDIR /app
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose any necessary ports (optional, based on your app requirements)
EXPOSE 4444

# Run the application
CMD ["python", "main.py"]