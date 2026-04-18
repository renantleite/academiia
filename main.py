from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def raiz():
    return {"ok": True}
