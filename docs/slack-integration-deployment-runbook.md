# Slack Integration Deployment Runbook

**Feature:** Bidirectional Slack <-> Board messaging via OAuth
**Target:** Helm production (`helm-api.blueorangedigital.co`)
**Server:** EC2 `i-0cd90a1fe58457497` (via AWS SSM)
**Estimated time:** 30-45 minutes

---

## Prerequisites

- [ ] AWS SSM access to EC2 instance
- [ ] AWS Secrets Manager write access (`/openclaw/mission-control/*`)
- [ ] Cloudflare dashboard access (Zero Trust + DNS)
- [ ] Slack workspace admin access (to create the app)

---

## Step 1: Create the Slack App

1. Go to https://api.slack.com/apps
2. Click **Create New App** > **From a manifest**
3. Select the target Slack workspace
4. Paste the contents of `docs/slack-app-manifest.yaml` (switch to YAML tab)
5. Review and click **Create**

After creation, collect these values from the app settings:

| Value | Where to find it |
|-------|-----------------|
| **Client ID** | Settings > Basic Information > App Credentials |
| **Client Secret** | Settings > Basic Information > App Credentials |
| **Signing Secret** | Settings > Basic Information > App Credentials |
| **App-Level Token** | Settings > Basic Information > App-Level Tokens > Generate Token (scope: `connections:write`) |

> **Important:** When generating the App-Level Token, name it `socket-mode` and grant the `connections:write` scope. This is the `SLACK_APP_TOKEN` (starts with `xapp-`).

---

## Step 2: Store Secrets in AWS Secrets Manager

Run from a machine with AWS CLI configured for `us-east-2`:

```bash
REGION="us-east-2"

# Replace placeholders with actual values from Step 1

aws secretsmanager create-secret \
  --name "/openclaw/mission-control/slack-client-id" \
  --description "Slack app client ID for Helm OAuth" \
  --secret-string "YOUR_CLIENT_ID" \
  --region "$REGION" \
  --tags Key=Project,Value=openclaw Key=Component,Value=slack

aws secretsmanager create-secret \
  --name "/openclaw/mission-control/slack-client-secret" \
  --description "Slack app client secret for Helm OAuth" \
  --secret-string "YOUR_CLIENT_SECRET" \
  --region "$REGION" \
  --tags Key=Project,Value=openclaw Key=Component,Value=slack

aws secretsmanager create-secret \
  --name "/openclaw/mission-control/slack-app-token" \
  --description "Slack app-level token for Socket Mode (xapp-...)" \
  --secret-string "xapp-YOUR_APP_TOKEN" \
  --region "$REGION" \
  --tags Key=Project,Value=openclaw Key=Component,Value=slack

aws secretsmanager create-secret \
  --name "/openclaw/mission-control/slack-signing-secret" \
  --description "Slack signing secret for request verification" \
  --secret-string "YOUR_SIGNING_SECRET" \
  --region "$REGION" \
  --tags Key=Project,Value=openclaw Key=Component,Value=slack

# Generate a Fernet-compatible encryption key for token storage
aws secretsmanager create-secret \
  --name "/openclaw/mission-control/encryption-key" \
  --description "Fernet encryption key for Slack bot token storage" \
  --secret-string "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --region "$REGION" \
  --tags Key=Project,Value=openclaw Key=Component,Value=slack
```

> **Note:** If `cryptography` isn't installed locally, generate the key on the EC2 server instead:
> ```bash
> python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

### Verify secrets exist

```bash
aws secretsmanager list-secrets \
  --region us-east-2 \
  --filters Key=name,Values=/openclaw/mission-control/slack \
  --query 'SecretList[].Name' --output table
```

Expected output — 5 secrets:
```
slack-client-id
slack-client-secret
slack-app-token
slack-signing-secret
encryption-key
```

---

## Step 3: Cloudflare Zero Trust Bypass

The OAuth callback (`/api/v1/slack/oauth/callback`) is hit by Slack's redirect — it cannot pass Cloudflare Access authentication. We secure it with a signed JWT state parameter instead.

1. Go to https://one.dash.cloudflare.com > **Access** > **Applications**
2. Find the **OpenClaw Helm - Core** application
3. Edit the application and add a **Bypass** policy:

| Setting | Value |
|---------|-------|
| **Policy name** | Slack OAuth Callback Bypass |
| **Action** | Bypass |
| **Selector** | Path |
| **Value** | `/api/v1/slack/oauth/callback` |

Alternatively, add a path-based exception rule:

```
Rule: Bypass
Selector: URI Path
Operator: is
Value: /api/v1/slack/oauth/callback
```

4. Save the policy.

### Verify

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "https://helm-api.blueorangedigital.co/api/v1/slack/oauth/callback"
```

Expected: `400` (missing params) — NOT `302` (Zero Trust redirect). If you get `302`, the bypass rule isn't active yet.

---

## Step 4: Deploy Code to Server

### Connect to the EC2 instance

```bash
aws ssm start-session --target i-0cd90a1fe58457497
sudo -iu openclaw
```

### Pull latest code

```bash
cd /home/openclaw/mission-control
git pull origin master
```

### Pull Slack secrets into .env

```bash
bash /home/openclaw/mission-control/scripts/pull-slack-secrets.sh
```

Verify the secrets were appended:

```bash
grep SLACK_ ~/infrastructure/secrets/mission-control.env
```

Expected — 5 SLACK_ vars plus ENCRYPTION_KEY and SLACK_OAUTH_REDIRECT_URI.

### Rebuild and restart containers

```bash
cd /home/openclaw/mission-control

# Rebuild backend and webhook-worker (they share the same image)
docker compose build backend

# Restart with new code + env vars
docker compose up -d backend webhook-worker
```

### Verify containers are running

```bash
docker compose ps
```

All 5 services should show `Up`:
- `db` (healthy)
- `redis` (healthy)
- `backend` (Up)
- `frontend` (Up)
- `webhook-worker` (Up)

### Verify migration ran

Check backend logs for migration output:

```bash
docker compose logs backend --tail 30 | grep -i migration
```

Expected: `Running database migrations` and `Database migrations complete`.

### Verify Socket Mode listener started

```bash
docker compose logs webhook-worker --tail 30 | grep -i slack
```

Expected: `slack.socket.thread_started` and `slack.socket.connected`.

---

## Step 5: Verify the Integration

### 5.1: Health check

```bash
curl -s https://helm-api.blueorangedigital.co/health | python3 -m json.tool
```

Expected: `{"ok": true}`

### 5.2: OAuth flow

1. Open the Helm UI: https://helm.blueorangedigital.co
2. Navigate to any board > **Edit** (gear icon or `/boards/{id}/edit`)
3. Scroll down to the **Slack Integration** section
4. Click **Connect Slack**
5. Authorize the app in the Slack consent screen
6. You should be redirected back to the board edit page with `?slack=connected`
7. The Slack Integration section should now show the workspace name and channel

### 5.3: Verify DB record

On the server:

```bash
docker compose exec db psql -U postgres -d mission_control -c \
  "SELECT id, board_id, slack_team_name, slack_channel_name, is_active FROM board_slack_connections;"
```

Expected: one row with the board you connected, `is_active = true`.

### 5.4: Inbound message (Slack -> Board)

1. Post a message in the connected Slack channel
2. Check webhook-worker logs:
   ```bash
   docker compose logs webhook-worker --tail 20 | grep slack
   ```
   Expected: `slack.socket.message_enqueued` and `slack.dispatch.sent_to_agent`
3. Verify the message appears in the board's chat / agent receives it

### 5.5: Outbound message (Board -> Slack)

1. Type a message in the board UI chat
2. Verify it appears in the connected Slack channel (with agent attribution)
3. If the message originated from Slack, verify the agent response is a **thread reply** (not a new message)

### 5.6: Disconnect

1. Go to board edit page > Slack Integration > **Disconnect**
2. Verify the connection is removed from the DB
3. Verify messages stop syncing

---

## Rollback

If something goes wrong:

### Quick disable (keep code, disable Slack)

Remove Slack env vars from the running containers:

```bash
cd /home/openclaw/mission-control
# Edit compose override or just unset the vars
docker compose exec backend env | grep SLACK  # verify they exist
docker compose up -d backend webhook-worker   # restart without Slack vars in .env
```

Or simply clear the Slack lines from `~/infrastructure/secrets/mission-control.env` and restart.

### Full rollback (revert code)

```bash
cd /home/openclaw/mission-control
git log --oneline -5                    # find the previous commit
git revert HEAD                         # revert the Slack commit
docker compose build backend
docker compose up -d backend webhook-worker
```

### Database rollback

```bash
docker compose exec backend alembic downgrade fa6e83f8d9a1
```

This drops the `board_slack_connections` table.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "Slack integration is not configured" in UI | `SLACK_CLIENT_ID` not set — verify `grep SLACK_ ~/infrastructure/secrets/mission-control.env` and container env |
| OAuth redirects to Cloudflare login | Zero Trust bypass not active for `/api/v1/slack/oauth/callback` — check Step 3 |
| "Invalid or expired state parameter" | OAuth took >10 minutes, or `SLACK_CLIENT_SECRET` mismatch between authorize and callback |
| Socket Mode not connecting | `SLACK_APP_TOKEN` missing or invalid — must start with `xapp-` and have `connections:write` scope |
| Messages not arriving from Slack | Check `docker compose logs webhook-worker` for `slack.socket.*` entries; verify the channel is connected in DB |
| Agent responses not posting to Slack | Check `ENCRYPTION_KEY` is valid Fernet key; verify `bot_token_encrypted` can be decrypted; check `slack.outbound.*` logs |
| Token revoked / `invalid_auth` errors | Re-run OAuth flow — click **Connect Slack** again from the board edit page |

---

## Secrets Inventory (Updated)

| Secret Path | Component |
|------------|-----------|
| `/openclaw/mission-control/slack-client-id` | Backend + webhook-worker |
| `/openclaw/mission-control/slack-client-secret` | Backend (OAuth + JWT state signing) |
| `/openclaw/mission-control/slack-app-token` | Webhook-worker (Socket Mode) |
| `/openclaw/mission-control/slack-signing-secret` | Backend (request verification) |
| `/openclaw/mission-control/encryption-key` | Backend + webhook-worker (Fernet token encryption) |
