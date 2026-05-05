#!/usr/bin/env bash
# EC2 deployment setup script for RealityAI Multi-Agent System
# Tested on: Amazon Linux 2023 / Ubuntu 22.04+
#
# Usage:
#   scp -r deploy/ ec2-user@<ip>:~/
#   ssh ec2-user@<ip>
#   chmod +x deploy/ec2-setup.sh
#   ./deploy/ec2-setup.sh

set -euo pipefail

echo "=== RealityAI EC2 Setup ==="

# --- Install Docker ---
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    if command -v yum &>/dev/null; then
        sudo yum install -y docker
        sudo systemctl enable docker
        sudo systemctl start docker
        sudo usermod -aG docker "$USER"
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update
        sudo apt-get install -y docker.io docker-compose-plugin
        sudo systemctl enable docker
        sudo systemctl start docker
        sudo usermod -aG docker "$USER"
    fi
    echo "Docker installed. You may need to log out and back in for group changes."
else
    echo "Docker already installed."
fi

# --- Install Docker Compose plugin ---
if ! docker compose version &>/dev/null; then
    echo "Installing Docker Compose plugin..."
    DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
    mkdir -p "$DOCKER_CONFIG/cli-plugins"
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
        -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
    chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
fi

echo "Docker Compose version: $(docker compose version)"

# --- Clone repo ---
REPO_DIR="$HOME/realityai-agents"
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning repository..."
    git clone git@github.com:C-byChloe/realityai-agents.git "$REPO_DIR"
else
    echo "Repository exists. Pulling latest..."
    cd "$REPO_DIR" && git pull
fi

cd "$REPO_DIR"

# --- Configure environment ---
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo ""
    echo "IMPORTANT: Edit .env to set:"
    echo "  - ANTHROPIC_API_KEY"
    echo "  - JWT_SECRET_KEY (generate with: openssl rand -hex 32)"
    echo "  - DB_PASSWORD (change from default)"
    echo ""
    echo "Run: nano .env"
else
    echo ".env already exists."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys and secrets"
echo "  2. Run: docker compose up -d --build"
echo "  3. Access: http://<your-ec2-ip>:3000"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f          # View all logs"
echo "  docker compose ps               # Check service status"
echo "  docker compose down              # Stop all services"
echo "  docker compose up -d --build    # Rebuild and restart"
