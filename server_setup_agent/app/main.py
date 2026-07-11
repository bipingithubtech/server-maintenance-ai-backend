from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.chat import router as chat_router
from app.api.connect import router as connect_router

app = FastAPI(
    title="AI Server Maintenance Agent API",
    description="Backend API for the Server Maintenance AI Agent.",
    version="1.0.0"
)

# Allow frontend (Vite on 5173, or any origin in dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api/v1")
app.include_router(connect_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "AI Server Setup Agent API is running!"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
