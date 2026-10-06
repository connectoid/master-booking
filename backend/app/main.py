from fastapi import FastAPI

app = FastAPI(title="master-booking")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
