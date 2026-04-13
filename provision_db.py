"""Provision a Render PostgreSQL database for the sample auth app.

Usage:
    python sample/provision_db.py

Reads RENDER_API_KEY and RENDER_OWNER_ID from backend/.env,
creates a PostgreSQL instance on Render, waits for it to become
available, and writes the connection string to sample/sample-backend/.env.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the backend directory to the Python path so we can import RenderDatabase
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv

# Load credentials from backend/.env
load_dotenv(backend_dir / ".env")

RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID")


async def main():
    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        print("ERROR: RENDER_API_KEY and RENDER_OWNER_ID must be set in backend/.env")
        sys.exit(1)

    from app.storage.render_db import RenderDatabase

    db = RenderDatabase(api_key=RENDER_API_KEY, owner_id=RENDER_OWNER_ID)

    print("Creating Render PostgreSQL instance...")
    db_id = await db.create("sample-auth-app")
    if not db_id:
        print("ERROR: Failed to create database")
        sys.exit(1)

    print(f"Database ID: {db_id}")
    print("Waiting for database to be available (may take 1-3 minutes)...")

    is_ready = await db.wait_until_available(db_id, timeout=300)
    if not is_ready:
        print("ERROR: Database did not become available within timeout")
        sys.exit(1)

    print("Database is ready! Retrieving connection info...")
    conn_info = await db.get_connection_info(db_id)
    if not conn_info:
        print("ERROR: Could not retrieve connection info")
        sys.exit(1)

    external_url = conn_info.get("externalConnectionString", "")
    internal_url = conn_info.get("internalConnectionString", "")

    print(f"\nExternal URL: {external_url}")
    print(f"Internal URL: {internal_url}")

    # Write to sample-backend/.env
    env_path = Path(__file__).resolve().parent / "sample-backend" / ".env"
    env_path.write_text(
        f"DATABASE_URL={external_url}\n"
        f"FRONTEND_URL=http://localhost:3000\n"
    )
    print(f"\nWritten to {env_path}")
    print("\nDone! You can now start the sample backend:")
    print("  cd sample/sample-backend")
    print("  pip install -r requirements.txt")
    print("  uvicorn main:app --reload --port 8000")


if __name__ == "__main__":
    asyncio.run(main())
