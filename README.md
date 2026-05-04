# SCALE India Investment

This repo contains:

- `frontend`: React/CRACO app
- `backend`: FastAPI app
- `mongo`: required database for users, portfolios, holdings, trades, and watchlists

## Emergent cleanup

The repo no longer depends on Emergent-specific packages or config:

- removed `@emergentbase/visual-edits` from the frontend
- removed the CRACO visual-edits wrapper
- removed `emergentintegrations` from the backend

## Local setup

### 1. Prerequisites

Install these on your machine first:

- Python 3.11+
- Node.js 20+
- Yarn 1.x or Corepack-enabled Yarn
- MongoDB 7+ or a Mongo-compatible connection string

### 2. Backend

From [`backend/.env.example`](backend/.env.example), create `backend/.env` and set at least:

- `MONGO_URL`
- `DB_NAME`
- `JWT_SECRET`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Optional email variables:

- `ADMIN_EMAIL`
- `BREVO_API_KEY`
- `SENDER_EMAIL`
- `REPLY_TO_EMAIL`
- `PUBLIC_APP_URL`

Install and run:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8001
```

Backend will be available at `http://localhost:8001`.

### 3. Frontend

From [`frontend/.env.example`](frontend/.env.example), create `frontend/.env`.

For local dev, keep:

```env
REACT_APP_BACKEND_URL=http://localhost:8001
```

Install and run:

```powershell
cd frontend
corepack enable
corepack yarn install
corepack yarn start
```

Frontend will be available at `http://localhost:3000`.

### 4. Local login

On first backend start, the app seeds an admin account using:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Use that account to log in and approve new users.
If you change those admin env values later, restarting the backend will sync the stored admin credentials to match.

## Docker self-hosting

This repo includes:

- [`backend/Dockerfile`](backend/Dockerfile)
- [`frontend/Dockerfile`](frontend/Dockerfile)
- [`frontend/nginx.conf`](frontend/nginx.conf)
- [`docker-compose.yml`](docker-compose.yml)

### 1. Prepare env

Create `backend/.env` from [`backend/.env.example`](backend/.env.example).

Recommended minimum:

```env
MONGO_URL=mongodb://mongo:27017
DB_NAME=scale
JWT_SECRET=replace-with-a-long-random-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-password
CORS_ORIGINS=http://localhost:8080
PUBLIC_APP_URL=http://localhost:8080
```

If you will expose this on a real domain:

```env
CORS_ORIGINS=https://scale.example.com
PUBLIC_APP_URL=https://scale.example.com
```

### 2. Start it

From the repo root:

```powershell
docker compose up --build -d
```

### 3. Open it

- App: `http://localhost:8080`

The frontend container proxies `/api` and `/api/ws` to the backend. The backend is not published directly in the default compose file.

## CasaOS guide

CasaOS is easiest if you run this as a Docker Compose app on the server itself.

### 1. Copy the repo to the server

Example:

```bash
cd /DATA/AppData
git clone <your-repo-url> scale
cd scale
```

If you are not using git on the server, copy the whole project folder into something like `/DATA/AppData/scale`.

### 2. Create and edit the backend env file

```bash
cd /DATA/AppData/scale/backend
cp .env.example .env
```

Set at least:

```env
MONGO_URL=mongodb://mongo:27017
DB_NAME=scale
JWT_SECRET=use-a-long-random-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=use-a-strong-password
```

Then set the public URL values to match how you will access the app.

If you are using the server IP and port:

```env
CORS_ORIGINS=http://192.168.1.50:8080
PUBLIC_APP_URL=http://192.168.1.50:8080
```

If you are using a domain behind a reverse proxy:

```env
CORS_ORIGINS=https://scale.yourdomain.com
PUBLIC_APP_URL=https://scale.yourdomain.com
```

### 3. Set the public port for the frontend container

From the repo root on the server:

```bash
export APP_PORT=8080
export PUBLIC_APP_URL=http://192.168.1.50:8080
```

Or with a domain:

```bash
export APP_PORT=8080
export PUBLIC_APP_URL=https://scale.yourdomain.com
```

### 4. Build and start on CasaOS

From the repo root:

```bash
docker compose up --build -d
```

### 5. Add it to CasaOS

You can manage it in either of these ways:

1. Use CasaOS terminal or SSH and manage it with `docker compose`.
2. Use CasaOS Custom Install / Compose import if your CasaOS version supports compose-based apps.

If you use the CasaOS UI route, make sure the repo files already exist on the server because this setup builds from source.

### 6. Reverse proxy

If you use CasaOS with Nginx Proxy Manager, Caddy, Traefik, or another proxy:

- point the domain to the frontend container port
- keep the backend private
- leave `/api` and `/api/ws` on the same host as the frontend

WebSockets are already handled by the included Nginx config in the frontend container.

### 7. Updating on CasaOS

```bash
cd /DATA/AppData/scale
git pull
docker compose up --build -d
```

### 8. Backup

Back up:

- `backend/.env`
- the Docker volume `mongo_data`

## Production notes

- Change `JWT_SECRET` and `ADMIN_PASSWORD` before exposing the app.
- Email notifications are skipped unless `BREVO_API_KEY` and related email settings are configured.
- The backend fetches live market data from NSE and Yahoo Finance, so outbound internet access is required.
- If you use a reverse proxy, route traffic to the frontend container and do not split the API onto a different host unless you also update CORS and the frontend base URL.

## What I could not fully verify here

I updated the repo and Docker/CasaOS docs, but I could not complete full package installation or container builds in this sandbox because outbound package downloads and Docker access are restricted here.
