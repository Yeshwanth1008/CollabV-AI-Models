@echo off
echo Starting CollabV AI...
if not exist .env copy .env.example .env
pip install -r requirements.txt -q
uvicorn collabv.api:app --port 8000 --reload
pause
