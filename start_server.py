#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from config.settings import get_settings
cfg = get_settings()
print(f"API host: {cfg.api.host}")
print(f"API port: {cfg.api.port}")
print(f"CORS origins: {cfg.api.cors_origins}")
print("Config OK - ready to start uvicorn")