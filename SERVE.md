# Python API — SERVE (RDP / VPS)

Flask backend. Port: **5000**. Host: `0.0.0.0`.

**Important:** Is API ko **HTTP** pe chalao (`http://...`). Self-signed HTTPS mat lagao — browser / AgencyWA pe SSL certificate errors aa jayenge.

## Role

- REST API for React admin
- WhatsApp webhooks, chatbot logic, users, bookings
- AgencyWA Customer API for WA sessions / send / QR

## Setup (RDP server)

### 1. Clone

```bash
git clone https://github.com/raheelshehzad188/python-api.git
cd python-api
```

### 2. Python deps

```bash
pip3 install -r requirements.txt
```

Windows (RDP) pe agar `pip3` na ho:

```bat
py -m pip install -r requirements.txt
```

### 3. MySQL + DB dump (repo mein included)

```bash
mysql -u root -e "CREATE DATABASE IF NOT EXISTS python_learning;"
mysql -u root python_learning < python_learning.sql
```

Windows (XAMPP / MySQL path adjust karein):

```bat
mysql -u root -e "CREATE DATABASE IF NOT EXISTS python_learning;"
mysql -u root python_learning < python_learning.sql
```

### 4. Config

```bash
cp config.example.py config.py
```

Edit `config.py`:

| Key | Value |
|-----|--------|
| `DB_*` | Local MySQL credentials |
| `WA_APP_PUBLIC_URL` | `http://YOUR_PUBLIC_IP:5000` (HTTP, not HTTPS) |
| AgencyWA keys | From AgencyWA panel |

### 5. Run (HTTP only — no SSL errors)

```bash
python3 main.py
```

Windows:

```bat
py main.py
```

API: `http://127.0.0.1:5000`  
Remote: `http://YOUR_SERVER_IP:5000`

Firewall mein **TCP 5000** allow karein. URL hamesha `http://` se kholen — `https://` mat use karo jab tak real SSL cert na ho.

### 6. Background (optional)

Linux / WSL:

```bash
pip3 install waitress
# or: nohup python3 main.py &
pm2 start main.py --name python-api --interpreter python3
```

Windows Task Scheduler / NSSM se `py main.py` auto-start kar sakte ho.

## AgencyWA

Site Settings (React) ya `config.py`:

- `agency_api_base_url` — e.g. `http://localhost/agencywa/api`
- `agency_api_key` / `agency_api_secret`
- `wa_app_public_url` — **public HTTP** base (AgencyWA is URL pe webhook POST karega)

```
React → Python (:5000 HTTP) → AgencyWA API → WhatsApp
AgencyWA webhook → http://YOUR_IP:5000/webhooks/... → AI reply → AgencyWA send
```

## SSL errors avoid karne ke rules

1. API URL: `http://IP:5000` — self-signed HTTPS mat chalao  
2. React `.env`: `VITE_API_BASE_URL=http://YOUR_IP:5000`  
3. Agar React HTTPS pe hai aur mixed-content block ho: same domain pe HTTP reverse proxy / PHP proxy use karo, ya React bhi HTTP pe serve karo  
4. Jab real domain + Let's Encrypt mil jaye tab hi HTTPS on karna
