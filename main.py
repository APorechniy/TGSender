import os
import json
import secrets
from typing import Dict, Optional
from google import genai
from fastapi import FastAPI, Header, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel, Field

# Скрытый динамический путь из переменных окружения
SECRET_ROUTE = os.getenv("SECRET_ROUTE_PATH", "default-hidden-route-xyz123")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

app = FastAPI(
    docs_url=None, 
    redoc_url=None, 
    openapi_url=None
)

# Разрешаем CORS-запросы с фронтенда, включая заголовок X-API-Key
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# --- ИСХОДНЫЕ СХЕМЫ ДЛЯ ТЕЛЕГРАМА ---
class MessageRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=20)
    answers: Optional[Dict[str, str]] = Field(default=None)
    message: Optional[str] = Field(default=None, max_length=1000)
    workersCount: Optional[str] = Field(default=None, max_length=50)
    agreement: bool

# --- НОВЫЕ СХЕМЫ ДЛЯ GEMINI ---
class PreviewRequest(BaseModel):
    niche: str = Field(..., min_length=2, max_length=50)
    goal: str = Field(..., min_length=2, max_length=50)
    vibe: str = Field(..., min_length=2, max_length=50)
    usp: str = Field(..., min_length=0, max_length=150)

class ClientConfig(BaseModel):
    client_name: str
    telegram_token: str
    telegram_chat_id: str

# In-memory хранилище сгенерированных концептов для истории (в продакшене лучше заменить на БД/Redis)
generation_history: Dict[str, dict] = {}

def load_clients_dynamically() -> Dict[str, dict]:
    try:
        with open("clients.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

async def verify_client(
    x_api_key: str = Header(..., alias="X-API-Key"),
    clients_db: dict = Depends(load_clients_dynamically)
) -> ClientConfig:
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
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in str(text))

async def send_telegram_message(payload: MessageRequest, client: ClientConfig):
    url = f"https://api.telegram.org/bot{client.telegram_token}/sendMessage"
    
    esc_name = escape_markdown_v2(payload.name)
    esc_phone = escape_markdown_v2(payload.phone)
    esc_agreement = escape_markdown_v2("Да" if payload.agreement else "Нет")
    
    message_lines = [
        "*Новая заявка\\!*",
        "",
        f"*Имя:* {esc_name}",
        f"*Телефон:* {esc_phone}"
    ]
    
    if payload.workersCount:
        esc_workers = escape_markdown_v2(payload.workersCount)
        message_lines.append(f"*Количество сотрудников:* {esc_workers}")
        
    if payload.message and payload.message.strip():
        esc_msg = escape_markdown_v2(payload.message.strip())
        message_lines.append("")
        message_lines.append(f"*Сообщение:* {esc_msg}")
        
    if payload.answers:
        answers_block = []
        for question, answer in payload.answers.items():
            if answer and answer.strip():
                esc_question = escape_markdown_v2(question.strip())
                esc_answer = escape_markdown_v2(answer.strip())
                answers_block.append(f"• *{esc_question}:* {esc_answer}")
        
        if answers_block:
            message_lines.append("")
            message_lines.append("*Ответы на квиз:*")
            message_lines.extend(answers_block)
            
    message_lines.append("")
    message_lines.append(f"*Согласие:* {esc_agreement}")
    
    formatted_text = "\n".join(message_lines)
    
    headers = {"Content-Type": "application/json"}
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

# --- РОУТ ДЛЯ ТЕЛЕГРАМА (ПОЛНОСТЬЮ СОХРАНЕН) ---
@app.post(f"/webhook/{SECRET_ROUTE}", status_code=status.HTTP_200_OK)
async def handle_message(
    payload: MessageRequest, 
    client: ClientConfig = Depends(verify_client)
):
    telegram_result = await send_telegram_message(payload, client)
    return {
        "success": True,
        "client": client.client_name,
        "telegram_response": telegram_result
    }

# --- НОВЫЙ РОУТ ДЛЯ РАБОТЫ С GEMINI ---
@app.post(f"/generate-preview/{SECRET_ROUTE}", status_code=status.HTTP_200_OK)
async def generate_preview(
    payload: PreviewRequest,
    client: ClientConfig = Depends(verify_client)
):
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini API key is not configured on the server"
        )

    # 1. Сжатый маппинг параметров для ИИ
    niche_map = {
        "saas": "SaaS product or AI automation tool",
        "fintech": "Crypto, investments, or modern banking platform",
        "edtech": "Expert online school or immersive bootcamps",
        "ecom": "Trendy direct-to-consumer e-commerce brand",
    }
    goal_map = {
        "leads": "capture contact leads with a high-conversion newsletter form",
        "sales": "sell a single digital product/service directly",
        "app": "drive installations of a mobile app",
    }
    vibe_map = {
        "tech": "Futuristic Cyberpunk style with deep dark background (#090d16), vibrant neon purple/cyan gradients, glowing borders, high-tech fonts",
        "minimal": "Intellectual Minimalist design, pure white background, heavy reliance on sophisticated typography, sleek grey/black elements",
        "lux": "Ultra-luxurious dark mode, charcoal/gold accents (#d4af37), elegant serif headings, smooth cards, extremely premium aesthetic",
    }

    niche_str = niche_map.get(payload.niche, "general business")
    goal_str = goal_map.get(payload.goal, "capture leads")
    vibe_str = vibe_map.get(payload.vibe, "clean and modern")
    
    # Экранируем возможные HTML-теги в УТП
    sanitized_usp = payload.usp.replace("<", "&lt;").replace(">", "&gt;")

    # 2. Формирование промпта
    prompt = f"""Create a fully styled single-section landing page markup. 
Niche: {niche_str}
Goal: {goal_str}
Design Theme Vibe: {vibe_str}
Key Value Proposition (USP): "{sanitized_usp}"

Strict Guidelines:
1. Return JSON format: {{"html": "HTML_STRING"}}
2. Do not write markdown blocks (no ```html) inside the JSON value.
3. The HTML must load Tailwind CSS via CDN: <script src="https://cdn.tailwindcss.com"></script>.
4. Build a clean responsive layout containing:
   - Modern navbar (with logo, placeholder nav, and clean outline button).
   - Hero section (Headline, detailed subheadline, input field + CTA button based on the Goal, and clean micro-features/trust-badges).
5. Ensure the styling perfectly matches the "Design Theme Vibe" color codes. Keep code concise, clean, and under 2200 characters total. Do not include heavy scripts."""

    # 3. Отправка запроса в Google Gemini REST API
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    body = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    async with httpx.AsyncClient() as http_client:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt,

            )
            
            gemini_res = response.text
            
            # Извлекаем сырой текст JSON из ответа Gemini
            raw_text = gemini_res['candidates'][0]['content']['parts'][0]['text']
            parsed_json = json.loads(raw_text)
            html_content = parsed_json.get("html", "")
            
            # 4. Генерация уникального ID сессии и сохранение истории
            generation_id = f"landing_{secrets.token_hex(3)}_{client.client_name[:3].lower()}"
            
            # generation_history[generation_id] = {
            #     "client": client.client_name,
            #     "niche": payload.niche,
            #     "goal": payload.goal,
            #     "vibe": payload.vibe,
            #     "usp": payload.usp,
            #     "html": html_content
            # }

            return {
                "id": generation_id,
                "html": html_content
            }

        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Gemini API returned error: {exc.response.text}"
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Internal generator error: {str(exc)}"
            )