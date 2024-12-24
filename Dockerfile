# Use an official Python runtime as the base image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project into the container
COPY . .

# Expose the necessary port (Koyeb assigns ports dynamically, so it's optional)
EXPOSE 8000

# Command to run your bot
CMD ["python", "bot.py"]
