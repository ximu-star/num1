from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contract_review.agent.factory import create_contract_review_agent
from contract_review.app.routers import admin, run
from contract_review.infrastructure.init_db import init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 建表（SQLite 开箱即用；生产可切 PostgreSQL）
    await init_database()
    # 2. 初始化共享 Agent
    app.state.agent = create_contract_review_agent()
    yield


app = FastAPI(title="Contract Review AI Service", lifespan=lifespan)

app.include_router(run.router)
app.include_router(admin.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
