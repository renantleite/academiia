from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
import motor.motor_asyncio

app = FastAPI(title="App Treino Familiar")

# --- CONEXÃO COM O MONGODB ---
MONGO_URL = "mongodb+srv://renantleite:renan123@cluster0.drmw3vv.mongodb.net/?appName=Cluster0"
cliente = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
banco = cliente.app_fitness
colecao_treinos = banco.treinos
colecao_usuarios = banco.usuarios 

# --- MODELAGEM DE DADOS ---
class Usuario(BaseModel):
    nome: str
    pin: str 

class Login(BaseModel):
    nome: str
    pin: str

# NOVOS MODELOS PARA CONFIGURAÇÕES
class AlteraPin(BaseModel):
    pin_atual: str
    novo_pin: str

class ConfirmacaoAcao(BaseModel):
    pin: str

class Serie(BaseModel):
    reps: int
    carga_kg: float

class Exercicio(BaseModel):
    nome: str
    series: List[Serie]

class Treino(BaseModel):
    usuario: str 
    data: str = datetime.now().strftime("%Y-%m-%d")
    grupo_muscular: str
    exercicios: List[Exercicio]

# ==========================================
# ROTAS DE USUÁRIOS (SISTEMA DE PIN)
# ==========================================
@app.post("/usuarios/criar")
async def criar_usuario(usuario: Usuario):
    existe = await colecao_usuarios.find_one({"nome": usuario.nome})
    if existe:
        raise HTTPException(status_code=400, detail="Este nome já está em uso.")
    await colecao_usuarios.insert_one(usuario.model_dump())
    return {"status": "sucesso"}

@app.get("/usuarios/listar")
async def listar_usuarios():
    cursor = colecao_usuarios.find({}, {"nome": 1, "_id": 0})
    usuarios = await cursor.to_list(length=100)
    return [u["nome"] for u in usuarios]

@app.post("/usuarios/login")
async def fazer_login(login: Login):
    usuario_bd = await colecao_usuarios.find_one({"nome": login.nome, "pin": login.pin})
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    return {"status": "sucesso"}

# ==========================================
# NOVAS ROTAS DE CONFIGURAÇÃO DE PERFIL
# ==========================================
@app.put("/usuarios/{nome}/pin")
async def alterar_pin(nome: str, dados: AlteraPin):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": dados.pin_atual})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN atual incorreto.")
    
    await colecao_usuarios.update_one({"nome": nome}, {"$set": {"pin": dados.novo_pin}})
    return {"status": "sucesso"}

@app.post("/usuarios/{nome}/resetar")
async def resetar_historico(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    
    # Apaga todos os treinos, mas mantém o perfil
    await colecao_treinos.delete_many({"usuario": nome})
    return {"status": "sucesso"}

@app.post("/usuarios/{nome}/deletar")
async def deletar_perfil(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    
    # Apaga o perfil E todos os treinos da pessoa
    await colecao_usuarios.delete_one({"nome": nome})
    await colecao_treinos.delete_many({"usuario": nome})
    return {"status": "sucesso"}

# ==========================================
# ROTAS DE TREINO
# ==========================================
@app.post("/treino/")
async def registrar_treino(treino: Treino):
    await colecao_treinos.insert_one(treino.model_dump())
    return {"status": "sucesso"}
@app.get("/treino/ultimo/{usuario}/{nome_exercicio}")
async def buscar_ultima_carga(usuario: str, nome_exercicio: str):
    # Busca o treino mais recente desse usuário que contenha o exercício selecionado
    filtro = {
        "usuario": usuario,
        "exercicios.nome": {"$regex": f"^{nome_exercicio}$", "$options": "i"}
    }
    ultimo_treino = await colecao_treinos.find_one(filtro, sort=[("data", -1)])
    
    if not ultimo_treino:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")
        
    for ex in ultimo_treino["exercicios"]:
        if ex["nome"].lower() == nome_exercicio.lower():
            return {
                "exercicio": ex["nome"],
                "ultimo_treino": ultimo_treino["data"],
                "series": ex["series"]
            }
@app.get("/treino/datas-treinadas/{usuario}")
async def buscar_datas_treinadas(usuario: str):
    datas = await colecao_treinos.distinct("data", {"usuario": usuario})
    return sorted(datas)

@app.get("/treino/sessao/data/{usuario}/{data_busca}")
async def buscar_treino_por_data(usuario: str, data_busca: str):
    cursor = colecao_treinos.find({"usuario": usuario, "data": data_busca})
    treinos_do_dia = await cursor.to_list(length=100)
    if not treinos_do_dia:
        raise HTTPException(status_code=404, detail="Vazio")
    
    exercicios_consolidados = []
    grupos_musculares = set()
    for t in treinos_do_dia:
        grupos_musculares.add(t["grupo_muscular"])
        for ex in t["exercicios"]:
            exercicios_consolidados.append(ex)
            
    return {
        "data": data_busca,
        "grupos_musculares": list(grupos_musculares),
        "exercicios": exercicios_consolidados
    }