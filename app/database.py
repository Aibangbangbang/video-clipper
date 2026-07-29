"""数据库模型"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Text, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from pathlib import Path

from app.config import config

Base = declarative_base()

DB_PATH = Path(__file__).parent.parent / "video_clipper.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Video(Base):
    __tablename__ = "videos"
    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    filepath = Column(String, nullable=False)
    duration = Column(Float, default=0)
    status = Column(String, default="uploaded")  # uploaded/transcribing/transcribed/processing/done/error
    created_at = Column(Text, default=lambda: datetime.now(timezone.utc).isoformat())
    updated_at = Column(Text, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    video_id = Column(String, nullable=False, index=True)
    segments = Column(JSON, default=list)     # [{start, end, text, source}]
    ocr_segments = Column(JSON, default=list)  # OCR 提取的字幕段
    full_text = Column(Text, default="")
    language = Column(String, default="zh")


class ClipResult(Base):
    """剪辑结果记录"""
    __tablename__ = "clip_results"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    video_id = Column(String, nullable=False, index=True)
    clip_type = Column(String, nullable=False)  # silence / keyword / combo / learn
    keep_ranges = Column(JSON, default=list)    # 保留的区间 [{start, end}]
    removed_ranges = Column(JSON, default=list)  # 删除的区间
    output_path = Column(String, nullable=True)
    params = Column(JSON, default=dict)         # 参数快照
    created_at = Column(Text, default=lambda: datetime.now(timezone.utc).isoformat())


class LearningTemplate(Base):
    """学习模板 - 从参考视频学到的文案结构和剪辑规则"""
    __tablename__ = "learning_templates"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)             # 模板名称
    description = Column(Text, default="")            # 模板描述
    source_video_id = Column(String, nullable=True)   # 学习来源视频
    delete_roles = Column(JSON, default=list)         # 要删除的角色 ["filler","repeat"]
    keep_roles = Column(JSON, default=list)           # 保留的角色
    role_stats = Column(JSON, default=dict)           # 角色统计 {role:{count,duration,ratio,examples}}
    analyzed_segments = Column(JSON, default=list)    # 来源视频的完整分析（含角色标签）
    created_at = Column(Text, default=lambda: datetime.now(timezone.utc).isoformat())
    updated_at = Column(Text, nullable=True)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
