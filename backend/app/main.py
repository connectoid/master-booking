from fastapi import FastAPI

from app.api.public import router as public_router

app = FastAPI(title="Окошко — запись к мастеру")
app.include_router(public_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
