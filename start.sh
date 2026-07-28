#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH=$(pwd)
export HF_ENDPOINT=https://hf-mirror.com
# CUDA 库路径（faster-whisper GPU 模式需要 libcublas）
export LD_LIBRARY_PATH="$HOME/.local/lib/python3.10/site-packages/nvidia/cublas/lib:$HOME/.local/lib/python3.10/site-packages/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH}"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
