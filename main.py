import os
import json
import secrets
from typing import List
from fastapi import FastAPI, Header, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel, Field

# 1. Считываем настройки скрытого пути из переменных окружения
# Рекомендуется сгенерировать длинную случайную строку, например uuid4
SECRET_ROUTE = os.getenv("SECRET_ROUTE_PATH", "default-hidden-route-xyz123")

# 2. Инициализируем FastAPI с отключенной документацией
app = FastAPI(
    docs_url=None, 
    redoc_url=None, 
    openapi_url=None
)

# Ограничиваем CORS. Если запросы идут не из браузера, можно оставить пустым.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене замените на конкретные домены или оставьте пустым
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Загрузка базы авторизованных клиентов
try:
    with open("clients.json", "r") as f:
        CLIENTS_DB = json.load(f)
except Exception as e:
    # Фоллбек на случай отсутствия файла (для тестов)
    CLIENTS_DB = {"test-token": "Test_Client"}

# Схема валидации входящего запроса (Pydantic v2)
class MessageRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=20)
    answers: List[str] = Field(default_factory=list)
    message: str = Field(..., max_length=1000)
    agreement: bool

# Зависимость для аутентификации ("Who-is-who")
async def verify_client(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    # Безопасное сравнение токенов для исключения timing attacks
    for token, client_name in CLIENTS_DB.items():
        if secrets.compare_digest(token, x_api_key):
            return client_name
    
    # Возвращаем стандартный 401/404, чтобы не давать зацепки атакующему
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized access"
    )

# Заглушка для отправки во внешний сервис
async def send_to_external_api(payload: MessageRequest, client_name: str):
    external_url = "https://api.external-resource-in-other-country.com/v1/send"
    
    # Сборка тела запроса для целевого API (настройте под свои нужды)
    headers = {
        "Authorization": "Bearer EXTERNAL_API_KEY_HERE",
        "Content-Type": "application/json"
    }
    
    # Для демонстрации выведем лог в консоль сервера
    print(f"[DEBUG] Отправка данных от имени '{client_name}' во внешний API...")
    
    # Раскомментируйте код ниже для реальной отправки:
    # async with httpx.AsyncClient() as client:
    #     try:
    #         response = await client.post(
    #             external_url, 
    #             json=payload.model_dump(), 
    #             headers=headers,
    #             timeout=10.0
    #         )
    #         response.raise_for_status()
    #         return response.json()
    #     except httpx.HTTPStatusError as exc:
    #         raise HTTPException(status_code=502, detail=f"External API error: {exc.response.status_code}")
    #     except httpx.RequestError as exc:
    #         raise HTTPException(status_code=503, detail="External API unavailable")
    
    return {"status": "simulated_success"}

# Единственный рабочий эндпоинт на скрытом пути
@app.post(f"/webhook/{SECRET_ROUTE}", status_code=status.HTTP_200_OK)
async def handle_message(
    payload: MessageRequest, 
    client_name: str = Depends(verify_client)
):
    # Данные валидированы Pydantic, клиент успешно аутентифицирован
    result = await send_to_external_api(payload, client_name)
    
    return {
        "success": True,
        "client": client_name,
        "external_response": result
    }