"""Chat endpoint for the Incident Advisor (RAG over past incidents' diagnosis
reports, via agent_system.agents.chatbot). Separate from investigation.py's
automated pipeline — this is a read-only, on-demand Q&A layer over incidents
the pipeline already produced; it never diagnoses or writes anything."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_system.agents.chatbot import answer_question

router = APIRouter(prefix="/api/chat", tags=["incident-chatbot"])


class ChatRequest(BaseModel):
    message: str


class ChatSource(BaseModel):
    incident_id: str | None = None
    job_name: str | None = None
    build_number: int | None = None
    status: str | None = None
    created_at: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message must not be empty")

    try:
        result = answer_question(message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    return ChatResponse(**result)
