"""配置加载"""
import os
from pathlib import Path
from dataclasses import dataclass, field
import yaml

# HuggingFace 国内镜像（解决直连不可达问题）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# CUDA 库路径（WSL 环境缺少 libcublas，从 pip 包中加载）
_nvidia_base = os.path.expanduser("~/.local/lib/python3.10/site-packages/nvidia")
if os.path.isdir(_nvidia_base):
    _lib_paths = []
    for _sub in os.listdir(_nvidia_base):
        _lib_dir = os.path.join(_nvidia_base, _sub, "lib")
        if os.path.isdir(_lib_dir):
            _lib_paths.append(_lib_dir)
    if _lib_paths:
        _existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = ":".join(_lib_paths) + (":" + _existing if _existing else "")


@dataclass
class WhisperConfig:
    model_size: str = "medium"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "zh"


@dataclass
class FFmpegConfig:
    codec: str = "libx264"
    crf: int = 23


@dataclass
class SilenceConfig:
    noise_db: float = -30
    min_duration: float = 0.5


@dataclass
class PathsConfig:
    uploads: str = "./uploads"
    output: str = "./output"
    temp: str = "./temp"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class Config:
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    ffmpeg: FFmpegConfig = field(default_factory=FFmpegConfig)
    silence: SilenceConfig = field(default_factory=SilenceConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def load_config() -> Config:
    """从 config.yaml 加载配置"""
    cfg = Config()
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 逐段映射
        if "whisper" in data:
            for k, v in data["whisper"].items():
                setattr(cfg.whisper, k, v)
        if "ffmpeg" in data:
            for k, v in data["ffmpeg"].items():
                setattr(cfg.ffmpeg, k, v)
        if "silence" in data:
            for k, v in data["silence"].items():
                setattr(cfg.silence, k, v)
        if "paths" in data:
            for k, v in data["paths"].items():
                setattr(cfg.paths, k, v)
        if "server" in data:
            for k, v in data["server"].items():
                setattr(cfg.server, k, v)
    return cfg


# 确保目录存在
def ensure_dirs(cfg: Config):
    base = Path(__file__).parent.parent
    for p in [cfg.paths.uploads, cfg.paths.output, cfg.paths.temp]:
        (base / p).mkdir(parents=True, exist_ok=True)


config = load_config()
ensure_dirs(config)
