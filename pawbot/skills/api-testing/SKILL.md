---
name: api-testing
description: "Test and debug HTTP APIs using curl, httpie, and jq. Send GET/POST/PUT/DELETE requests, inspect headers, parse JSON responses, handle authentication, and test webhooks. Use when the user asks to test an API endpoint, debug HTTP requests, work with REST or GraphQL APIs, or parse JSON data."
metadata: {"pawbot":{"emoji":"🧪","requires":{"bins":["curl"]}}}
---

# API Testing

Test HTTP APIs using `curl` and `jq` via the `exec` tool.

## GET Request

```bash
curl -s https://api.example.com/users | jq .
```

With headers:
```bash
curl -s -H "Authorization: Bearer TOKEN" https://api.example.com/me | jq .
```

## POST Request

JSON body:
```bash
curl -s -X POST https://api.example.com/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "email": "alice@example.com"}' | jq .
```

Form data:
```bash
curl -s -X POST https://api.example.com/upload \
  -F "file=@/path/to/file.pdf" \
  -F "description=Report"
```

## PUT / PATCH / DELETE

```bash
curl -s -X PUT https://api.example.com/users/1 \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated"}' | jq .

curl -s -X DELETE https://api.example.com/users/1 -w "\n%{http_code}\n"
```

## Inspect Response

Show headers + body:
```bash
curl -sv https://api.example.com/health 2>&1
```

Show status code only:
```bash
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health
```

Time the request:
```bash
curl -s -o /dev/null -w "time_total: %{time_total}s\n" https://api.example.com/health
```

## jq Filtering

```bash
# Extract field
curl -s https://api.example.com/users | jq '.[0].name'

# Filter array
curl -s https://api.example.com/users | jq '[.[] | select(.active == true)]'

# Count results
curl -s https://api.example.com/users | jq 'length'

# Extract multiple fields
curl -s https://api.example.com/users | jq '.[] | {name, email}'
```

## Authentication

Bearer token:
```bash
curl -s -H "Authorization: Bearer $TOKEN" https://api.example.com/me
```

Basic auth:
```bash
curl -s -u "user:password" https://api.example.com/admin
```

API key header:
```bash
curl -s -H "X-API-Key: YOUR_KEY" https://api.example.com/data
```

## GraphQL

```bash
curl -s -X POST https://api.example.com/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ users { id name email } }"}' | jq .
```

## Tips

- Add `-s` (silent) to suppress progress bars
- Use `jq .` for pretty-printed JSON
- Use `-w "\n%{http_code}\n"` to always see the status code
- Pipe responses through `jq` for structured parsing
- Use `httpie` (`http`) as a friendlier alternative if installed
