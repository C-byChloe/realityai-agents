# Deployment Guide

## Prerequisites

- AWS account with EC2 access
- SSH key pair configured
- Domain name (optional, for HTTPS)

## EC2 Deployment

### 1. Launch EC2 Instance

- **AMI**: Amazon Linux 2023 or Ubuntu 22.04
- **Instance type**: t3.medium (minimum) or t3.large (recommended)
- **Storage**: 30 GB gp3
- **Security group** inbound rules:
  - SSH (22) — your IP only
  - HTTP (80) — 0.0.0.0/0 (if using nginx reverse proxy)
  - Custom TCP (3000) — 0.0.0.0/0 (dashboard)
  - Custom TCP (8000) — 0.0.0.0/0 (API gateway)

### 2. Setup

```bash
# Copy setup script
scp deploy/ec2-setup.sh ec2-user@<ip>:~/

# SSH in and run
ssh ec2-user@<ip>
chmod +x ec2-setup.sh
./ec2-setup.sh
```

### 3. Configure

```bash
cd ~/realityai-agents
nano .env
```

Required settings:
- `ANTHROPIC_API_KEY` — your Claude API key
- `JWT_SECRET_KEY` — generate with `openssl rand -hex 32`
- `DB_PASSWORD` — change from default

### 4. Launch

```bash
docker compose up -d --build
```

### 5. Verify

```bash
# Check all services are running
docker compose ps

# Check health
curl http://localhost:8000/health

# View logs
docker compose logs -f
```

## EKS Deployment (Advanced)

For Kubernetes deployment, each service has its own Dockerfile. Key considerations:

1. **Container Registry**: Push images to ECR
   ```bash
   aws ecr create-repository --repository-name realityai/api-gateway
   aws ecr create-repository --repository-name realityai/agent-core
   aws ecr create-repository --repository-name realityai/core-service
   aws ecr create-repository --repository-name realityai/web-dashboard
   ```

2. **Kubernetes Resources** (create manifests for):
   - Deployments for each service
   - Services (ClusterIP for internal, LoadBalancer for gateway)
   - ConfigMap for non-secret env vars
   - Secret for API keys and passwords
   - PersistentVolumeClaims for PostgreSQL, ChromaDB, Redis
   - Ingress for external access

3. **Managed Services** (recommended for production):
   - Amazon RDS for PostgreSQL (instead of container)
   - Amazon ElastiCache for Redis (instead of container)
   - EBS/EFS for ChromaDB persistence

## Monitoring

- **LangSmith**: Set `LANGSMITH_TRACING=true` for agent traces
- **Docker logs**: `docker compose logs -f <service>`
- **Health endpoint**: `GET /health` returns status of all services

## Updating

```bash
cd ~/realityai-agents
git pull
docker compose up -d --build
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Services won't start | Check `docker compose logs <service>` |
| Health check failing | Verify `.env` has correct values |
| gRPC connection refused | core-service may still be starting (30s startup) |
| Out of memory | Upgrade to t3.large or add swap |
| Port conflict | Check `sudo lsof -i :<port>` |
