import os

from admin_api import app

if __name__ == "__main__":
    # HTTP only by default — avoids SSL certificate errors on RDP / IP access.
    # Set FLASK_DEBUG=1 for local development.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=debug,
    )