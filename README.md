# 🖨️ STL Hub

A self-hosted 3D printing file manager with AI chat assistance. Built as a learning project.

## Features

- **Google OAuth** login (no passwords to manage)
- **File browser** — upload, download, organize STL / GCode / 3MF files
- **Drag & drop** upload
- **AI Chat** — choose between 4 models:
  - Claude Sonnet 4.6 (Anthropic)
  - Claude Opus 4.6 (Anthropic)
  - GPT-4o (OpenAI)
  - GPT-4o Mini (OpenAI)
- **Terminal** — run shell commands directly on the server from the browser
- **FTP access** via vsftpd for direct file transfers

## Quick Deploy

```bash
git clone https://github.com/kkers42/FTP_Server.git
cd FTP_Server
bash deploy.sh
```

Then edit `/opt/stl-hub/.env` with your API keys.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `OPENAI_API_KEY` | From platform.openai.com |
| `APP_BASE_URL` | Your server URL (for OAuth redirect) |
| `ALLOWED_EMAILS` | Comma-separated allowed emails (blank = any Google account) |

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create a new OAuth 2.0 Client ID (Web application)
3. Add authorized redirect URI: `http://YOUR_IP:8080/auth/google/callback`
4. Copy Client ID and Secret to `.env`

## Stack

- **Backend**: FastAPI (Python)
- **Frontend**: Vanilla HTML/CSS/JS (no frameworks)
- **AI**: Anthropic SDK + OpenAI SDK
- **Auth**: Google OAuth 2.0 + JWT cookies
- **File server**: vsftpd
- **Process manager**: systemd

## License

MIT — open source, learn freely.
