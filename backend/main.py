from contextlib import asynccontextmanager
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.runtime.agent_graph import chat_agent
from api.agent import router as agent_router
from api.telemetry import router as telemetry_router
from config.settings import settings
from db.database import close_engine
from services.mqtt_subscriber import telemetry_subscriber


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start external integrations on boot and release resources on shutdown."""
    await chat_agent.setup()
    telemetry_subscriber.start()
    try:
        yield
    finally:
        telemetry_subscriber.stop()
        await chat_agent.close()
        await close_engine()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry_router, prefix="/api")
app.include_router(agent_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "status": "running",
        "mqtt_topic": settings.mqtt_topic,
    }
