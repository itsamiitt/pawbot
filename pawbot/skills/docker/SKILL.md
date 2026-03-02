---
name: docker
description: "Manage Docker containers and images: build, run, stop, inspect, compose, clean up, view logs. Use when the user asks to run containers, build images, manage Docker Compose stacks, debug container issues, or clean up Docker resources."
metadata: {"pawbot":{"emoji":"🐳","requires":{"bins":["docker"]}}}
---

# Docker

Manage containers, images, and Compose stacks via the `exec` tool.

## Containers

Run a container:
```bash
docker run -d --name myapp -p 8080:80 nginx:latest
```

List running containers:
```bash
docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}"
```

View logs:
```bash
docker logs --tail 100 -f myapp
```

Stop and remove:
```bash
docker stop myapp && docker rm myapp
```

Exec into a running container:
```bash
docker exec -it myapp /bin/sh
```

## Images

Build:
```bash
docker build -t myapp:latest .
```

List images:
```bash
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
```

## Docker Compose

Start stack:
```bash
docker compose up -d
```

View status:
```bash
docker compose ps
```

View logs:
```bash
docker compose logs --tail 50
```

Stop stack:
```bash
docker compose down
```

Rebuild and restart one service:
```bash
docker compose up -d --build myservice
```

## Cleanup

Remove stopped containers, unused images, and dangling volumes:
```bash
docker system prune -af --volumes
```

## Debugging

Inspect a container:
```bash
docker inspect myapp --format '{{json .State}}'
```

Check resource usage:
```bash
docker stats --no-stream
```

## Tips

- Use `-d` (detached) for background containers
- Use `--restart unless-stopped` for persistent services
- Map volumes with `-v /host/path:/container/path` for data persistence
- Use `docker compose` (v2) over `docker-compose` (v1)
