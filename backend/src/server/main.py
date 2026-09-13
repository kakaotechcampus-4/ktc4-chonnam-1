from fastapi import FastAPI

from server.api import skill

app = FastAPI(title="smishing-check-bot")
app.include_router(skill.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
