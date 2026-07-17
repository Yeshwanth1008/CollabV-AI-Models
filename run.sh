#!/bin/bash
echo "Starting CollabV AI..."
[ ! -f .env ] && cp .env.example .env
pip install -r requirements.txt -q
uvicorn collabv.api:app --port 8000 --reload
