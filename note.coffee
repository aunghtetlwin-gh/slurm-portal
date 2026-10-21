source .venv/bin/activate

uvicorn app:app --host 127.0.0.1 --port 8001 --reload


pgrep -af 'uvicorn app:app.*8001'
pkill -f 'uvicorn app:app.*--port 8001'
ss -ltnp | grep 8001


scontrol token lifespan=300
SLURM_JWT=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3OTI1NjE4OTEsImlhdCI6MTc5MjU2MTU5MSwic3VuIjoibGFpbGFpIn0.icp3QOT72vcLjVWJJwBQxIbSF9T2pkzZL5hRMc_eZTg


.venv/bin/pip install argon2-cffi

.venv/bin/python create_portal_users.py

.venv/bin/uvicorn test_auth_app:app \
  --host 127.0.0.1 \
  --port 8002