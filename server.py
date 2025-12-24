
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mercari_agent import MercariChatAgent
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the exact origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

# Shared agent instance
# Note: In a real multi-user env, we'd need session management.
# For this portfolio demo, a shared single-session agent is acceptable/expected.
agent = MercariChatAgent()

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        reply = await agent.chat(request.message)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"status": "Mercari Agent API is running", "docs": "/docs"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
