"""Photo, PDF and Office metadata transform server.

Run from this directory with:

    .venv/bin/python project.py

The seed URL is printed in the server log and is normally:

    https://127.0.0.1:3000/seed
"""

import os
from pathlib import Path

from maltego.server import MaltegoServerSettings, ServerHTTPSettings, run_server

# Importing the package executes the @register_transform decorators.
import transforms  # noqa: F401

BASE_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    settings = MaltegoServerSettings(
        server_name="Photo Metadata Toolkit",
        ns="homosapiens.photo_metadata",
        author="homosapiens",
        version="1.1.0",
        transform_execution_timeout=90,
        http_settings=ServerHTTPSettings(
            protocol="https",
            http_addr=os.getenv("PHOTO_HOST", "127.0.0.1"),
            http_port=int(os.getenv("PHOTO_PORT", "3000")),
            cert_file=str(BASE_DIR / "certs" / "cert.pem"),
            cert_key=str(BASE_DIR / "certs" / "key.pem"),
            cors_allowed_origins=["https://app.maltego.com"],
        ),
    )

    run_server(settings=settings)
