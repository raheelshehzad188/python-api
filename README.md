# python-api

Flask chatbot / WhatsApp API (AgencyWA). DB dump: `python_learning.sql`.

```bash
pip3 install -r requirements.txt
mysql -u root -e "CREATE DATABASE IF NOT EXISTS python_learning;"
mysql -u root python_learning < python_learning.sql
cp config.example.py config.py   # edit DB + AgencyWA
python3 main.py                  # http://0.0.0.0:5000
```

RDP / production: see [SERVE.md](SERVE.md). Serve over **HTTP** unless you have a valid SSL certificate.
