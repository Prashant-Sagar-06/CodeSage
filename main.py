"""
main.py
Entry point for the CodeSage PR Bot.
Starts the FastAPI server with Uvicorn.

Usage:
    uvicorn main:app --reload --port 8000
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.webhook import router as webhook_router

# ─── Load environment variables ───────────────────────────────────────────────
load_dotenv()

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="CodeSage PR Bot",
    description="AI-powered GitHub Pull Request reviewer using Groq + llama-3.3-70b",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# Register routes
app.include_router(webhook_router)


@app.on_event("startup")
async def on_startup():
    logger.info("🚀 CodeSage PR Bot starting up...")
    logger.info(f"   GROQ_API_KEY   : {'✓ set' if os.getenv('GROQ_API_KEY') else '✗ MISSING'}")
    logger.info(f"   GITHUB_TOKEN   : {'✓ set' if os.getenv('GITHUB_TOKEN') else '✗ MISSING'}")
    logger.info(f"   WEBHOOK_SECRET : {'✓ set' if os.getenv('WEBHOOK_SECRET') else '⚠ not set (insecure)'}")
    logger.info("   Listening for GitHub PR webhooks at POST /webhook")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("CodeSage PR Bot shutting down.")


# ─── Dev runner ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
