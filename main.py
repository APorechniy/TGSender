import os
import json
import secrets
from typing import List, Dict
from fastapi import FastAPI, Header, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel, Field

# Скрытый динамический путь из переменных окружения
SECRET_ROUTE = os.getenv("SECRET_ROUTE_PATH", "default-hidden-route-xyz123")

app = FastAPI(
    docs_url=None, 
    redoc_url=None, 
    openapi_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Описание структуры входящего запроса
class MessageRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=20)
    answers: List[str] = Field(default_factory=list)
    message: str = Field(..., max_length=1000)
    agreement: bool

# Контейнер для хранения параметров авторизованного клиента
class ClientConfig(BaseModel):
    client_name: str
    telegram_token: str
    telegram_chat_id: str

def load_clients_dynamically() -> Dict[str, dict]:
    """
    Динамически считывает файл clients.json при каждом запросе.
    Позволяет добавлять/удалять клиентов "на лету" без перезапуска сервера.
    """
    try:
        with open("clients.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # Возвращаем пустой словарь, если файл в процессе перезаписи
        return {}

async def verify_client(
    x_api_key: str = Header(..., alias="X-API-Key"),
    clients_db: dict = Depends(load_clients_dynamically)
) -> ClientConfig:
    """Аутентификация клиента по API-ключу с защитой от timing attacks."""
    for token, client_data in clients_db.items():
        if secrets.compare_digest(token, x_api_key):
            return ClientConfig(
                client_name=client_data.get("client_name", "Unknown"),
                telegram_token=client_data.get("telegram_token", ""),
                telegram_chat_id=str(client_data.get("telegram_chat_id", ""))
            )
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized access"
    )

def escape_markdown_v2(text: str) -> str:
    """
    Экранирует спецсимволы для корректной работы Telegram MarkdownV2.
    Без этого символы вроде '+', '.', '-' вызовут ошибку 400 Bad Request.
    """
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in str(text))

async def send_telegram_message(payload: MessageRequest, client: ClientConfig):
    # Формируем URL Telegram Bot API под конкретного клиента
    url = f"https://api.telegram.org/bot{client.telegram_token}/sendMessage"
    
    # Формируем текст сообщения с использованием Markdown-разметки.
    # Ключи оборачиваем в звездочки (жирный шрифт), а значения экранируем.
    formatted_text = (
        f"*Новая заявка\\!*\n\n"
        f"*Имя:* {escape_markdown_v2(payload.name)}\n"
        f"*Телефон:* {escape_markdown_v2(payload.phone)}\n"
        f"*Сообщение:* {escape_markdown_v2(payload.message)}\n"
        f"*Согласие:* {escape_markdown_v2('Да' if payload.agreement else 'Нет')}"
    )
    
    headers = {
        "Content-Type": "application/json"
    }
    
    body = {
        "chat_id": client.telegram_chat_id,
        "text": formatted_text,
        "parse_mode": "MarkdownV2"
    }
    
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(url, json=body, headers=headers, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Telegram API error: {exc.response.text}"
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram API is temporarily unavailable"
            )

@app.post(f"/webhook/{SECRET_ROUTE}", status_code=status.HTTP_200_OK)
async def handle_message(
    payload: MessageRequest, 
    client: ClientConfig = Depends(verify_client)
):
    # Отправка сформированного сообщения в Telegram
    telegram_result = await send_telegram_message(payload, client)
    
    return {
        "success": True,
        "client": client.client_name,
        "telegram_response": telegram_result
    }