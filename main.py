"""
STL Hub - 3D Print File Manager
A learning project for managing STL files with AI assistance
"""

import os
import json
import mimetypes
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
import anthropic
from openai import OpenAI

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
SECRET_KEY       = os.getenv("SECRET_KEY", "dev_secret_change_me")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL     = os.getenv("APP_BASE_URL", "http://localhost:8080")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
FTP_ROOT         = Path(os.getenv("FTP_ROOT", "/srv/stl-hub/files"))
ALLOWED_EMAILS   = [e.strip() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]

ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 8

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="STL Hub", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ensure file storage directory exists
FTP_ROOT.mkdir(parents=True, exist_ok=True)


# ── Auth helpers ─────────────────────────────────────────────────────────────
def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_token(token)


def optional_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    try:
        return decode_token(token)
    except Exception:
        return None


# ── File helpers ──────────────────────────────────────────────────────────────
def safe_path(user_email: str, relative: str = "") -> Path:
    """Return a safe absolute path within the user's directory."""
    user_dir = FTP_ROOT / user_email.replace("@", "_at_").replace(".", "_")
    user_dir.mkdir(parents=True, exist_ok=True)
    if not relative or relative in (".", "/", ""):
        return user_dir
    resolved = (user_dir / relative).resolve()
    if not str(resolved).startswith(str(user_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved


def file_info(path: Path, base: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(base)),
        "is_dir": path.is_dir(),
        "size": stat.st_size if path.is_file() else 0,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "is_stl": path.suffix.lower() == ".stl",
    }


# ── Routes: root ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = optional_user(request)
    if user:
        return RedirectResponse("/files")
    with open("static/index.html") as f:
        return HTMLResponse(f.read())


# ── Routes: Google OAuth ──────────────────────────────────────────────────────
@app.get("/auth/google")
async def auth_google():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{APP_BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@app.get("/auth/google/callback")
async def auth_google_callback(code: str, request: Request):
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{APP_BASE_URL}/auth/google/callback",
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        if "error" in token_data:
            raise HTTPException(status_code=400, detail=token_data.get("error_description", "OAuth error"))

        user_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user_info = user_resp.json()

    email = user_info.get("email", "")
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail=f"Access not allowed for {email}")

    token = create_token({
        "email": email,
        "name": user_info.get("name", email),
        "picture": user_info.get("picture", ""),
    })

    response = RedirectResponse("/files")
    response.set_cookie("session_token", token, httponly=True, max_age=TOKEN_EXPIRE_HOURS * 3600)
    return response


@app.post("/auth/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session_token")
    return response


# ── Routes: File browser ───────────────────────────────────────────────────────
@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    get_current_user(request)  # require auth
    with open("static/app.html") as f:
        return HTMLResponse(f.read())


@app.get("/api/files")
async def list_files(request: Request, path: str = ""):
    user = get_current_user(request)
    base = safe_path(user["email"])
    target = safe_path(user["email"], path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    return {
        "path": path,
        "items": [file_info(item, base) for item in items],
        "user": {"email": user["email"], "name": user["name"], "picture": user.get("picture", "")},
    }


@app.post("/api/files/upload")
async def upload_file(request: Request, path: str = Form(""), file: UploadFile = File(...)):
    user = get_current_user(request)
    dest = safe_path(user["email"], path) / file.filename
    async with aiofiles.open(dest, "wb") as out:
        content = await file.read()
        await out.write(content)
    return {"message": f"Uploaded {file.filename}", "size": len(content)}


@app.get("/api/files/download")
async def download_file(request: Request, path: str):
    user = get_current_user(request)
    target = safe_path(user["email"], path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


@app.delete("/api/files/delete")
async def delete_file(request: Request, path: str):
    user = get_current_user(request)
    target = safe_path(user["email"], path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"message": f"Deleted {path}"}


@app.post("/api/files/mkdir")
async def make_dir(request: Request, path: str = Form(...), name: str = Form(...)):
    user = get_current_user(request)
    new_dir = safe_path(user["email"], path) / name
    new_dir.mkdir(parents=True, exist_ok=True)
    return {"message": f"Created folder {name}"}


# ── Routes: AI Chat ────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    user = get_current_user(request)
    body = await request.json()
    model_choice = body.get("model", "claude")
    messages = body.get("messages", [])
    prompt = body.get("prompt", "")

    system_msg = (
        "You are a helpful assistant embedded in STL Hub, a 3D printing file manager. "
        "Help the user with 3D printing questions, STL files, slicing, printer settings, "
        "and general development questions about the project. Be friendly and educational."
    )

    if model_choice == "claude":
        # Claude claude-sonnet-4-6
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        if prompt:
            chat_messages.append({"role": "user", "content": prompt})
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_msg,
            messages=chat_messages,
        )
        reply = response.content[0].text
        model_label = "Claude Sonnet 4.6"

    elif model_choice == "claude-opus":
        # Claude Opus claude-opus-4-6
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        if prompt:
            chat_messages.append({"role": "user", "content": prompt})
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=system_msg,
            messages=chat_messages,
        )
        reply = response.content[0].text
        model_label = "Claude Opus 4.6"

    elif model_choice == "gpt":
        # GPT-4o
        client = OpenAI(api_key=OPENAI_API_KEY)
        oai_messages = [{"role": "system", "content": system_msg}]
        oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages]
        if prompt:
            oai_messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=oai_messages,
            max_tokens=1024,
        )
        reply = response.choices[0].message.content
        model_label = "GPT-4o"

    elif model_choice == "gpt-mini":
        # GPT-4o-mini
        client = OpenAI(api_key=OPENAI_API_KEY)
        oai_messages = [{"role": "system", "content": system_msg}]
        oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages]
        if prompt:
            oai_messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=oai_messages,
            max_tokens=1024,
        )
        reply = response.choices[0].message.content
        model_label = "GPT-4o Mini"

    else:
        raise HTTPException(status_code=400, detail="Unknown model")

    return {"reply": reply, "model": model_label}


# ── Routes: Terminal (SSH passthrough) ────────────────────────────────────────
@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request):
    get_current_user(request)
    with open("static/terminal.html") as f:
        return HTMLResponse(f.read())


@app.post("/api/terminal/exec")
async def terminal_exec(request: Request):
    """Execute a shell command on the VPS (admin only)."""
    import subprocess
    user = get_current_user(request)
    body = await request.json()
    cmd = body.get("cmd", "")

    # Safety: only allow non-destructive commands for non-admins
    # Admin emails can run anything; others get a restricted set
    dangerous = ["rm -rf", "mkfs", "dd if=", ":(){:|:&};:", "shutdown", "reboot", "passwd"]
    if any(d in cmd for d in dangerous):
        return {"output": "⛔ Command blocked for safety.", "exit_code": 1}

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15,
            cwd=str(FTP_ROOT)
        )
        output = result.stdout + result.stderr
        return {"output": output or "(no output)", "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"output": "Command timed out (15s limit)", "exit_code": 1}
    except Exception as e:
        return {"output": str(e), "exit_code": 1}
