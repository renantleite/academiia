from fastapi import FastAPI
import os
import motor.motor_asyncio

app = FastAPI(title="App Treino Familiar")

MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("A variável de ambiente MONGO_URL não foi configurada.")

cliente = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
banco = cliente.app_fitness
colecao_usuarios = banco.usuarios

@app.get("/")
async def raiz():
    return {"ok": True}

@app.get("/usuarios/listar")
async def listar_usuarios():
    cursor = colecao_usuarios.find({}, {"nome": 1, "_id": 0}).sort("nome", 1)
    usuarios = await cursor.to_list(length=200)
    return [u["nome"] for u in usuarios]
