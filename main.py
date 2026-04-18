from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime, timezone
import os
import math
import motor.motor_asyncio

app = FastAPI(title="App Treino Familiar")

# ==========================================
# CONEXÃO COM O MONGODB
# ==========================================
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("A variável de ambiente MONGO_URL não foi configurada.")

cliente = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
banco = cliente.app_fitness
colecao_treinos = banco.treinos
colecao_usuarios = banco.usuarios


# ==========================================
# HELPERS
# ==========================================
def formatar_data(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def calcular_volume_total(series: list[dict]) -> float:
    return round(sum((s["reps"] * s["carga_kg"]) for s in series), 2)


def buscar_melhor_serie(series: list[dict]) -> Optional[dict]:
    if not series:
        return None
    return sorted(series, key=lambda s: (s["carga_kg"], s["reps"]), reverse=True)[0]


def estimar_1rm(reps: int, carga_kg: float) -> float:
    if reps <= 1:
        return round(carga_kg, 2)
    return round(carga_kg * (1 + reps / 30), 2)


# ==========================================
# MODELAGEM DE DADOS
# ==========================================
class Usuario(BaseModel):
    nome: str = Field(..., min_length=1, max_length=50)
    pin: str

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nome não pode ser vazio.")
        return v

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class Login(BaseModel):
    nome: str
    pin: str

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class AlteraPin(BaseModel):
    pin_atual: str
    novo_pin: str

    @field_validator("pin_atual", "novo_pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class ConfirmacaoAcao(BaseModel):
    pin: str

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class Serie(BaseModel):
    reps: int = Field(..., gt=0, le=100)
    carga_kg: float = Field(..., ge=0, le=1000)


class Exercicio(BaseModel):
    nome: str = Field(..., min_length=1, max_length=100)
    series: List[Serie]


class Treino(BaseModel):
    usuario: str
    grupo_muscular: str
    exercicios: List[Exercicio]
    data_treino: Optional[datetime] = None

    @field_validator("usuario", "grupo_muscular")
    @classmethod
    def validar_texto(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


# ==========================================
# STARTUP: ÍNDICES
# ==========================================
@app.on_event("startup")
async def criar_indices():
    await colecao_usuarios.create_index("nome", unique=True)
    await colecao_treinos.create_index([("usuario", 1), ("data_treino", -1)])
    await colecao_treinos.create_index([("usuario", 1), ("grupo_muscular", 1)])
    await colecao_treinos.create_index([("usuario", 1), ("exercicios.nome", 1)])


# ==========================================
# ROTAS DE USUÁRIOS
# ==========================================
@app.post("/usuarios/criar")
async def criar_usuario(usuario: Usuario):
    existe = await colecao_usuarios.find_one({"nome": usuario.nome})
    if existe:
        raise HTTPException(status_code=400, detail="Este nome já está em uso.")

    await colecao_usuarios.insert_one(usuario.model_dump())
    return {
        "status": "sucesso",
        "mensagem": "Perfil criado com sucesso."
    }


@app.get("/usuarios/listar")
async def listar_usuarios():
    cursor = colecao_usuarios.find({}, {"nome": 1, "_id": 0}).sort("nome", 1)
    usuarios = await cursor.to_list(length=200)
    return [u["nome"] for u in usuarios]


@app.post("/usuarios/login")
async def fazer_login(login: Login):
    usuario_bd = await colecao_usuarios.find_one({"nome": login.nome, "pin": login.pin})
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    return {
        "status": "sucesso",
        "usuario": login.nome
    }


@app.put("/usuarios/{nome}/pin")
async def alterar_pin(nome: str, dados: AlteraPin):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": dados.pin_atual})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN atual incorreto.")

    await colecao_usuarios.update_one(
        {"nome": nome},
        {"$set": {"pin": dados.novo_pin}}
    )
    return {
        "status": "sucesso",
        "mensagem": "PIN alterado com sucesso."
    }


@app.post("/usuarios/{nome}/resetar")
async def resetar_historico(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    resultado = await colecao_treinos.delete_many({"usuario": nome})
    return {
        "status": "sucesso",
        "treinos_apagados": resultado.deleted_count
    }


@app.post("/usuarios/{nome}/deletar")
async def deletar_perfil(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    await colecao_usuarios.delete_one({"nome": nome})
    resultado_treinos = await colecao_treinos.delete_many({"usuario": nome})

    return {
        "status": "sucesso",
        "mensagem": "Conta excluída com sucesso.",
        "treinos_apagados": resultado_treinos.deleted_count
    }


# ==========================================
# ROTAS DE TREINO
# ==========================================
@app.post("/treino/")
async def registrar_treino(treino: Treino):
    data_treino = treino.data_treino or datetime.now(timezone.utc)

    documento = {
        "usuario": treino.usuario,
        "grupo_muscular": treino.grupo_muscular,
        "data_treino": data_treino,
        "exercicios": [ex.model_dump() for ex in treino.exercicios],
    }

    await colecao_treinos.insert_one(documento)

    return {
        "status": "sucesso",
        "mensagem": "Treino registrado com sucesso.",
        "data_treino": data_treino.isoformat()
    }


@app.get("/treino/ultimo/{usuario}/{nome_exercicio}")
async def buscar_ultima_carga(usuario: str, nome_exercicio: str):
    filtro = {
        "usuario": usuario,
        "exercicios.nome": {"$regex": f"^{nome_exercicio}$", "$options": "i"}
    }

    ultimo_treino = await colecao_treinos.find_one(
        filtro,
        sort=[("data_treino", -1)]
    )

    if not ultimo_treino:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")

    for ex in ultimo_treino["exercicios"]:
        if ex["nome"].lower() == nome_exercicio.lower():
            return {
                "exercicio": ex["nome"],
                "ultimo_treino": formatar_data(ultimo_treino["data_treino"]),
                "series": ex["series"]
            }

    raise HTTPException(status_code=404, detail="Exercício não encontrado.")


@app.get("/treino/resumo/{usuario}/{nome_exercicio}")
async def buscar_resumo_exercicio(usuario: str, nome_exercicio: str):
    filtro = {
        "usuario": usuario,
        "exercicios.nome": {"$regex": f"^{nome_exercicio}$", "$options": "i"}
    }

    cursor = colecao_treinos.find(filtro).sort("data_treino", -1)
    treinos = await cursor.to_list(length=50)

    if not treinos:
        raise HTTPException(status_code=404, detail="Nenhum histórico encontrado para este exercício.")

    sessoes = []

    for treino in treinos:
        for ex in treino["exercicios"]:
            if ex["nome"].lower() == nome_exercicio.lower():
                series = ex["series"]
                melhor_serie_sessao = buscar_melhor_serie(series)
                sessoes.append({
                    "data": formatar_data(treino["data_treino"]),
                    "series": series,
                    "volume_total": calcular_volume_total(series),
                    "total_series": len(series),
                    "melhor_serie": melhor_serie_sessao
                })

    if not sessoes:
        raise HTTPException(status_code=404, detail="Nenhum histórico encontrado para este exercício.")

    ultima_sessao = sessoes[0]

    todas_series = []
    for sessao in sessoes:
        todas_series.extend(sessao["series"])

    melhor_serie_geral = buscar_melhor_serie(todas_series)
    maior_carga = max((s["carga_kg"] for s in todas_series), default=0)

    ultimas_sessoes = [
        {
            "data": s["data"],
            "volume_total": s["volume_total"],
            "total_series": s["total_series"]
        }
        for s in sessoes[:3]
    ]

    return {
        "exercicio": nome_exercicio,
        "ultima_sessao": {
            "data": ultima_sessao["data"],
            "series": ultima_sessao["series"],
            "volume_total": ultima_sessao["volume_total"],
            "total_series": ultima_sessao["total_series"]
        },
        "melhor_serie": {
            "reps": melhor_serie_geral["reps"],
            "carga_kg": melhor_serie_geral["carga_kg"],
            "estimativa_1rm": estimar_1rm(
                melhor_serie_geral["reps"],
                melhor_serie_geral["carga_kg"]
            )
        } if melhor_serie_geral else None,
        "maior_carga": maior_carga,
        "ultimas_sessoes": ultimas_sessoes
    }


@app.get("/treino/datas-treinadas/{usuario}")
async def buscar_datas_treinadas(usuario: str):
    datas = await colecao_treinos.distinct("data_treino", {"usuario": usuario})
    datas_formatadas = sorted({formatar_data(d) for d in datas})
    return datas_formatadas


@app.get("/treino/sessao/data/{usuario}/{data_busca}")
async def buscar_treino_por_data(usuario: str, data_busca: str):
    try:
        data_inicio = datetime.strptime(data_busca, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")

    proximo_dia = datetime(
        year=data_inicio.year,
        month=data_inicio.month,
        day=data_inicio.day,
        tzinfo=timezone.utc
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    fim_dia = proximo_dia.replace(hour=23, minute=59, second=59, microsecond=999999)

    cursor = colecao_treinos.find({
        "usuario": usuario,
        "data_treino": {"$gte": proximo_dia, "$lte": fim_dia}
    }).sort("data_treino", 1)

    treinos_do_dia = await cursor.to_list(length=200)

    if not treinos_do_dia:
        raise HTTPException(status_code=404, detail="Nenhum treino encontrado nesta data.")

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
    }from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime, timezone
import os
import math
import motor.motor_asyncio

app = FastAPI(title="App Treino Familiar")

# ==========================================
# CONEXÃO COM O MONGODB
# ==========================================
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("A variável de ambiente MONGO_URL não foi configurada.")

cliente = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
banco = cliente.app_fitness
colecao_treinos = banco.treinos
colecao_usuarios = banco.usuarios


# ==========================================
# HELPERS
# ==========================================
def formatar_data(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def calcular_volume_total(series: list[dict]) -> float:
    return round(sum((s["reps"] * s["carga_kg"]) for s in series), 2)


def buscar_melhor_serie(series: list[dict]) -> Optional[dict]:
    if not series:
        return None
    return sorted(series, key=lambda s: (s["carga_kg"], s["reps"]), reverse=True)[0]


def estimar_1rm(reps: int, carga_kg: float) -> float:
    if reps <= 1:
        return round(carga_kg, 2)
    return round(carga_kg * (1 + reps / 30), 2)


# ==========================================
# MODELAGEM DE DADOS
# ==========================================
class Usuario(BaseModel):
    nome: str = Field(..., min_length=1, max_length=50)
    pin: str

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nome não pode ser vazio.")
        return v

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class Login(BaseModel):
    nome: str
    pin: str

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class AlteraPin(BaseModel):
    pin_atual: str
    novo_pin: str

    @field_validator("pin_atual", "novo_pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class ConfirmacaoAcao(BaseModel):
    pin: str

    @field_validator("pin")
    @classmethod
    def validar_pin(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("PIN deve ter exatamente 4 dígitos.")
        return v


class Serie(BaseModel):
    reps: int = Field(..., gt=0, le=100)
    carga_kg: float = Field(..., ge=0, le=1000)


class Exercicio(BaseModel):
    nome: str = Field(..., min_length=1, max_length=100)
    series: List[Serie]


class Treino(BaseModel):
    usuario: str
    grupo_muscular: str
    exercicios: List[Exercicio]
    data_treino: Optional[datetime] = None

    @field_validator("usuario", "grupo_muscular")
    @classmethod
    def validar_texto(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


# ==========================================
# STARTUP: ÍNDICES
# ==========================================
@app.on_event("startup")
async def criar_indices():
    await colecao_usuarios.create_index("nome", unique=True)
    await colecao_treinos.create_index([("usuario", 1), ("data_treino", -1)])
    await colecao_treinos.create_index([("usuario", 1), ("grupo_muscular", 1)])
    await colecao_treinos.create_index([("usuario", 1), ("exercicios.nome", 1)])


# ==========================================
# ROTAS DE USUÁRIOS
# ==========================================
@app.post("/usuarios/criar")
async def criar_usuario(usuario: Usuario):
    existe = await colecao_usuarios.find_one({"nome": usuario.nome})
    if existe:
        raise HTTPException(status_code=400, detail="Este nome já está em uso.")

    await colecao_usuarios.insert_one(usuario.model_dump())
    return {
        "status": "sucesso",
        "mensagem": "Perfil criado com sucesso."
    }


@app.get("/usuarios/listar")
async def listar_usuarios():
    cursor = colecao_usuarios.find({}, {"nome": 1, "_id": 0}).sort("nome", 1)
    usuarios = await cursor.to_list(length=200)
    return [u["nome"] for u in usuarios]


@app.post("/usuarios/login")
async def fazer_login(login: Login):
    usuario_bd = await colecao_usuarios.find_one({"nome": login.nome, "pin": login.pin})
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    return {
        "status": "sucesso",
        "usuario": login.nome
    }


@app.put("/usuarios/{nome}/pin")
async def alterar_pin(nome: str, dados: AlteraPin):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": dados.pin_atual})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN atual incorreto.")

    await colecao_usuarios.update_one(
        {"nome": nome},
        {"$set": {"pin": dados.novo_pin}}
    )
    return {
        "status": "sucesso",
        "mensagem": "PIN alterado com sucesso."
    }


@app.post("/usuarios/{nome}/resetar")
async def resetar_historico(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    resultado = await colecao_treinos.delete_many({"usuario": nome})
    return {
        "status": "sucesso",
        "treinos_apagados": resultado.deleted_count
    }


@app.post("/usuarios/{nome}/deletar")
async def deletar_perfil(nome: str, confirmacao: ConfirmacaoAcao):
    usuario = await colecao_usuarios.find_one({"nome": nome, "pin": confirmacao.pin})
    if not usuario:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    await colecao_usuarios.delete_one({"nome": nome})
    resultado_treinos = await colecao_treinos.delete_many({"usuario": nome})

    return {
        "status": "sucesso",
        "mensagem": "Conta excluída com sucesso.",
        "treinos_apagados": resultado_treinos.deleted_count
    }


# ==========================================
# ROTAS DE TREINO
# ==========================================
@app.post("/treino/")
async def registrar_treino(treino: Treino):
    data_treino = treino.data_treino or datetime.now(timezone.utc)

    documento = {
        "usuario": treino.usuario,
        "grupo_muscular": treino.grupo_muscular,
        "data_treino": data_treino,
        "exercicios": [ex.model_dump() for ex in treino.exercicios],
    }

    await colecao_treinos.insert_one(documento)

    return {
        "status": "sucesso",
        "mensagem": "Treino registrado com sucesso.",
        "data_treino": data_treino.isoformat()
    }


@app.get("/treino/ultimo/{usuario}/{nome_exercicio}")
async def buscar_ultima_carga(usuario: str, nome_exercicio: str):
    filtro = {
        "usuario": usuario,
        "exercicios.nome": {"$regex": f"^{nome_exercicio}$", "$options": "i"}
    }

    ultimo_treino = await colecao_treinos.find_one(
        filtro,
        sort=[("data_treino", -1)]
    )

    if not ultimo_treino:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")

    for ex in ultimo_treino["exercicios"]:
        if ex["nome"].lower() == nome_exercicio.lower():
            return {
                "exercicio": ex["nome"],
                "ultimo_treino": formatar_data(ultimo_treino["data_treino"]),
                "series": ex["series"]
            }

    raise HTTPException(status_code=404, detail="Exercício não encontrado.")


@app.get("/treino/resumo/{usuario}/{nome_exercicio}")
async def buscar_resumo_exercicio(usuario: str, nome_exercicio: str):
    filtro = {
        "usuario": usuario,
        "exercicios.nome": {"$regex": f"^{nome_exercicio}$", "$options": "i"}
    }

    cursor = colecao_treinos.find(filtro).sort("data_treino", -1)
    treinos = await cursor.to_list(length=50)

    if not treinos:
        raise HTTPException(status_code=404, detail="Nenhum histórico encontrado para este exercício.")

    sessoes = []

    for treino in treinos:
        for ex in treino["exercicios"]:
            if ex["nome"].lower() == nome_exercicio.lower():
                series = ex["series"]
                melhor_serie_sessao = buscar_melhor_serie(series)
                sessoes.append({
                    "data": formatar_data(treino["data_treino"]),
                    "series": series,
                    "volume_total": calcular_volume_total(series),
                    "total_series": len(series),
                    "melhor_serie": melhor_serie_sessao
                })

    if not sessoes:
        raise HTTPException(status_code=404, detail="Nenhum histórico encontrado para este exercício.")

    ultima_sessao = sessoes[0]

    todas_series = []
    for sessao in sessoes:
        todas_series.extend(sessao["series"])

    melhor_serie_geral = buscar_melhor_serie(todas_series)
    maior_carga = max((s["carga_kg"] for s in todas_series), default=0)

    ultimas_sessoes = [
        {
            "data": s["data"],
            "volume_total": s["volume_total"],
            "total_series": s["total_series"]
        }
        for s in sessoes[:3]
    ]

    return {
        "exercicio": nome_exercicio,
        "ultima_sessao": {
            "data": ultima_sessao["data"],
            "series": ultima_sessao["series"],
            "volume_total": ultima_sessao["volume_total"],
            "total_series": ultima_sessao["total_series"]
        },
        "melhor_serie": {
            "reps": melhor_serie_geral["reps"],
            "carga_kg": melhor_serie_geral["carga_kg"],
            "estimativa_1rm": estimar_1rm(
                melhor_serie_geral["reps"],
                melhor_serie_geral["carga_kg"]
            )
        } if melhor_serie_geral else None,
        "maior_carga": maior_carga,
        "ultimas_sessoes": ultimas_sessoes
    }


@app.get("/treino/datas-treinadas/{usuario}")
async def buscar_datas_treinadas(usuario: str):
    datas = await colecao_treinos.distinct("data_treino", {"usuario": usuario})
    datas_formatadas = sorted({formatar_data(d) for d in datas})
    return datas_formatadas


@app.get("/treino/sessao/data/{usuario}/{data_busca}")
async def buscar_treino_por_data(usuario: str, data_busca: str):
    try:
        data_inicio = datetime.strptime(data_busca, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")

    proximo_dia = datetime(
        year=data_inicio.year,
        month=data_inicio.month,
        day=data_inicio.day,
        tzinfo=timezone.utc
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    fim_dia = proximo_dia.replace(hour=23, minute=59, second=59, microsecond=999999)

    cursor = colecao_treinos.find({
        "usuario": usuario,
        "data_treino": {"$gte": proximo_dia, "$lte": fim_dia}
    }).sort("data_treino", 1)

    treinos_do_dia = await cursor.to_list(length=200)

    if not treinos_do_dia:
        raise HTTPException(status_code=404, detail="Nenhum treino encontrado nesta data.")

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
