---
name: deploy-pipeline
description: "Deploy applications to production via the deploy MCP server: deploy apps, generate nginx configs, run database migrations, create backups. Use when the user asks to deploy, configure reverse proxies, migrate databases, or manage production backups."
metadata: {"pawbot":{"emoji":"🚀","requires":{}}}
---

# Deploy Pipeline

Use the `deploy` MCP server for production deployment operations.

## Core Tools

| Tool | Purpose |
|------|---------|
| `deploy_app` | Deploy an application (git pull, build, restart) |
| `deploy_nginx_config` | Generate and install nginx reverse proxy config |
| `deploy_ssl_cert` | Request/renew SSL certificate via Let's Encrypt |
| `deploy_db_migrate` | Run database migrations |
| `deploy_db_backup` | Create database backup |
| `deploy_db_restore` | Restore from a backup |
| `deploy_rollback` | Roll back to previous deployment |
| `deploy_status` | Check deployment status |

## Workflow: Full Deployment

```
mcp_deploy_deploy_db_backup(database="myapp_prod")
mcp_deploy_deploy_db_migrate(path="/home/user/myapp", command="alembic upgrade head")
mcp_deploy_deploy_app(path="/home/user/myapp", build_cmd="pip install -e .", restart_cmd="systemctl restart myapp")
mcp_deploy_deploy_status(service="myapp")
```

## Workflow: Nginx + SSL Setup

```
mcp_deploy_deploy_nginx_config(domain="myapp.example.com", upstream_port=8000, ssl=true)
mcp_deploy_deploy_ssl_cert(domain="myapp.example.com", email="admin@example.com")
```

## Workflow: Rollback

If deployment fails:
```
mcp_deploy_deploy_rollback(service="myapp")
mcp_deploy_deploy_db_restore(database="myapp_prod", backup="myapp_prod_20240101.sql.gz")
```

## Tips

- Always backup the database before migrations
- Check `deploy_status` after each deployment step
- Use rollback if the health check fails post-deploy
- SSL certificates auto-renew via certbot cron
