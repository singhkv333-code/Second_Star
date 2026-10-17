# The remote browser behind the Browser widget (server.py). Built in Azure
# Container Registry and run in Azure Container Instances, apart from the VM
# that holds the chart and the user data (deploy/provision_browser.sh).
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.63.0 websockets==17.1
WORKDIR /app
COPY server.py egress.py /app/
# the image's unprivileged user; Chromium never runs as root
USER pwuser
ENV RB_PORT=5177 PYTHONUNBUFFERED=1
EXPOSE 5177
CMD ["python3", "-u", "server.py"]
