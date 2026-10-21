"""Isolated authentication smoke-test. Does not expose Slurm routes."""
from fastapi import FastAPI
from auth import router, init_db

init_db()
app = FastAPI(title='Slurm Portal Auth Test')
app.include_router(router)