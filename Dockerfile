# Use the official lightweight Python image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the application code and dependencies
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port for health checks or FastAPI if needed
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
