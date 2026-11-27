# Production secrets

One file per secret, containing only the value (no `NAME=`, a trailing newline is fine). Everything in this folder
except this README is gitignored, and none of it is copied into the image (`.dockerignore` is an allow-list).

| File | What | Where to get it |
|---|---|---|
| `groq_key` | Groq API key (generator) | console.groq.com → API Keys |
| `gemini_key` | Google AI Studio key (reviewer) | aistudio.google.com → Get API key |
| `tunnel_token` | Cloudflare Tunnel token | Zero Trust → Networks → Tunnels → your tunnel → install command (the long token after `--token`) |

Docker Compose mounts each file read-only at `/run/secrets/<name>` inside the container that needs it. The app reads
them there (`SECRETS_DIR`); they never become environment variables, so they don't show up in `docker inspect`.

```bash
chmod 600 deploy/secrets/*_key deploy/secrets/tunnel_token   # readable by you only on the host
```

On Docker Desktop (macOS/Windows) the container user can read the files regardless. On a Linux host, make them
readable by uid 10001 (the app user), e.g. `sudo chown 10001 deploy/secrets/*_key`.

**Rotating a key:** replace the file, then `docker compose -f deploy/compose.yaml up -d --force-recreate app`.
