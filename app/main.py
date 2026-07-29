"""FastAPI 主应用"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from app.database import init_db
from app.routers import videos, learn

BASE_DIR = Path(__file__).parent

app = FastAPI(title="视频智能剪辑系统")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(videos.router)
app.include_router(learn.router)


@app.on_event("startup")
def on_startup():
    init_db()
    print("[启动] 数据库初始化完成")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
