from fastapi import FastAPI
import uvicorn

from app.api.chat import router as chat_router

app = FastAPI(
    title="AI Server Maintenance Agent API",
    description="Backend API for the Server Maintenance AI Agent.",
    version="1.0.0"
)

# Include our chat routes under the /api/v1 prefix
app.include_router(chat_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "AI Server Setup Agent API is running!"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
