import os
import datetime
import math
import json
import logging
from typing import Optional, Dict, Any, List
import re

from fastapi import FastAPI, Depends, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc

# Импортируем модули настроек и базы данных
from tgbot.config import settings
from tgbot.database.db_api import db, get_utc_now
from tgbot.database.models import ShopItemType, QuestProgress, Step, Quest, PlayerLocationLog, ActiveQuest, User, City
from backend.auth import verify_telegram_init_data
from backend.admin_panel import setup_admin
from backend.map_admin_routes import setup_map_admin

# Конфигурация логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TMA_API")

app = FastAPI(
    title="Quest Sity API v2.0",
    description="Высоконагруженный игровой бэкенд с поддержкой Live-Ops, Node-логики и AR",
    version="2.1.0"
)

app.add_middleware(SessionMiddleware, secret_key=os.environ.get("ADMIN_SECRET_KEY", "fallback-secret-key"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Схемы ---
class LocationCheckSchema(BaseModel):
    latitude: float
    longitude: float

class AnswerSubmitSchema(BaseModel):
    answer: str

class ClassChangeSchema(BaseModel):
    rpg_class: str

# --- Admin API Schemas ---
class CreateQuestSchema(BaseModel):
    title: str = Field(..., min_length=1, max_length=150)
    description: str = Field(..., min_length=1, max_length=2000)
    latitude: float
    longitude: float
    min_level_required: int = Field(default=1, ge=1)
    max_speed_kmh: float = Field(default=15.0, gt=0)
    is_coop: bool = Field(default=False)
    is_published: bool = Field(default=False)

class CreateCitySchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    latitude: float
    longitude: float
    radius_km: float = Field(default=5.0, gt=0)
    is_active: bool = Field(default=True)


# --- Вспомогательные функции ---
def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

async def get_current_user(x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data")) -> dict:
    bot_token = settings.bot.token.get_secret_value()
    tg_user = verify_telegram_init_data(x_tg_init_data, bot_token)
    if not tg_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидная сессия Telegram. Доступ заблокирован."
        )
    return tg_user

def normalize_npc_dialogue(npc_dialogue: Any) -> Optional[Dict[str, Any]]:
    if not npc_dialogue: return None
    if hasattr(npc_dialogue, "model_dump"): npc_dialogue = npc_dialogue.model_dump()
    if isinstance(npc_dialogue, str):
        try: npc_dialogue = json.loads(npc_dialogue)
        except json.JSONDecodeError: return None
    if isinstance(npc_dialogue, dict): return npc_dialogue
    return None

def get_npc_start_node(npc_dialogue: Any) -> Optional[str]:
    dialogue = normalize_npc_dialogue(npc_dialogue)
    if not dialogue: return None
    if "start" in dialogue: return "start"
    return next(iter(dialogue.keys()), None)

def normalize_json_field(value: Any) -> Any:
    if value is None: return None
    if hasattr(value, "model_dump"): return value.model_dump()
    if isinstance(value, str):
        try: return json.loads(value)
        except Exception: return value
    return value

def generate_slug(text: str) -> str:
    """Генерирует slug из текста (используется для cities.slug)"""
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug.strip('-')


@app.on_event("startup")
async def on_startup():
    setup_admin(app, db.engine)
    setup_map_admin(app)
    logger.info("Веб-админпанель SQLAdmin подключена на /admin/")
    logger.info("Карта-основанная админпанель подключена на /admin-map/")
    await db.create_all()
    logger.info("База данных успешно синхронизирована.")


# =====================================================================
# АДМИН-ПАНЕЛЬ: LIVE-OPS, РАДАР И HEATMAPS (АНАЛИТИКА)
# =====================================================================

@app.get("/api/admin/radar")
async def get_live_radar():
    """Возвращает последние известные координаты активных игроков для карты в админке"""
    async with db.session_pool() as session:
        # Получаем логи за последние 60 минут
        time_threshold = get_utc_now() - datetime.timedelta(minutes=60)
        stmt = select(PlayerLocationLog, User.full_name, User.rpg_class).join(
            User, PlayerLocationLog.user_id == User.telegram_id
        ).where(PlayerLocationLog.timestamp >= time_threshold).order_by(desc(PlayerLocationLog.timestamp))
        
        results = await session.execute(stmt)
        
        players = {}
        for log, full_name, rpg_class in results.all():
            # Сохраняем только самую свежую точку для каждого пользователя
            if log.user_id not in players:
                players[log.user_id] = {
                    "user_id": log.user_id,
                    "name": full_name,
                    "class": rpg_class or "Без класса",
                    "lat": log.latitude,
                    "lng": log.longitude,
                    "quest_id": log.quest_id,
                    "time": log.timestamp.isoformat()
                }
        return list(players.values())

@app.get("/api/admin/heatmap/{quest_id}")
async def get_heatmap(quest_id: int):
    """Возвращает массив координат для построения тепловой карты конкретного квеста"""
    async with db.session_pool() as session:
        stmt = select(PlayerLocationLog.latitude, PlayerLocationLog.longitude).where(
            PlayerLocationLog.quest_id == quest_id
        )
        results = await session.execute(stmt)
        return [{"lat": r[0], "lng": r[1]} for r in results.all()]

@app.get("/api/admin/dicts")
async def get_admin_dicts():
    """Единый эндпоинт для автозаполнения и JS-редактора админки (Node-based)"""
    async with db.session_pool() as session:
        q_stmt = select(Quest.id, Quest.title).order_by(Quest.id)
        quests = [{"id": r[0], "title": f"[{r[0]}] {r[1]}"} for r in (await session.execute(q_stmt)).all()]
        
        s_stmt = select(Step.id, Step.instruction_text, Quest.title).join(Quest, Step.quest_id == Quest.id).order_by(Step.id)
        steps = [{"id": r[0], "text": f"[{r[2][:20]}] {r[1][:35]}..."} for r in (await session.execute(s_stmt)).all()]
        return {"quests": quests, "steps": steps}

@app.get("/api/admin/quests-map")
async def get_quests_for_map():
    """Возвращает все квесты с координатами для отображения на карте"""
    async with db.session_pool() as session:
        stmt = select(Quest.id, Quest.title, Quest.description, Quest.is_published, Quest.min_level_required).order_by(Quest.id)
        quests = await session.execute(stmt)
        
        result = []
        for quest_id, title, description, is_published, min_level in quests.all():
            # Получаем первый шаг квеста для координат
            step_stmt = select(Step.latitude, Step.longitude).where(Step.quest_id == quest_id).order_by(Step.id).limit(1)
            step_result = await session.execute(step_stmt)
            step_row = step_result.first()
            
            if step_row:
                result.append({
                    "id": quest_id,
                    "title": title,
                    "description": description,
                    "is_published": is_published,
                    "min_level_required": min_level,
                    "lat": step_row[0],
                    "lng": step_row[1],
                    "type": "quest"
                })
        
        return result

@app.get("/api/admin/cities")
async def get_cities_list():
    """Возвращает все города для отображения на карте"""
    async with db.session_pool() as session:
        stmt = select(City.id, City.name, City.latitude, City.longitude, City.radius_km, City.is_active).order_by(City.id)
        cities = await session.execute(stmt)
        
        result = []
        for city_id, name, lat, lng, radius, is_active in cities.all():
            result.append({
                "id": city_id,
                "name": name,
                "lat": lat,
                "lng": lng,
                "radius_km": radius,
                "is_active": is_active,
                "type": "city"
            })
        
        return result

@app.post("/api/admin/quests")
async def create_quest(req: CreateQuestSchema):
    """Создает новый квест через админ-панель карты"""
    async with db.session_pool() as session:
        # Проверяем, что квест с таким названием еще не существует
        existing = await session.execute(select(Quest).where(Quest.title == req.title))
        if existing.first():
            raise HTTPException(
                status_code=400,
                detail=f"Квест с названием '{req.title}' уже существует."
            )
        
        # Создаем новый квест
        new_quest = Quest(
            title=req.title,
            description=req.description,
            is_published=req.is_published,
            max_speed_kmh=req.max_speed_kmh,
            min_level_required=req.min_level_required,
            is_coop=req.is_coop,
            created_at=get_utc_now()
        )
        
        session.add(new_quest)
        await session.flush()
        
        # Создаем первый шаг квеста с координатами
        first_step = Step(
            quest_id=new_quest.id,
            instruction_text=f"Начало квеста: {req.title}",
            latitude=req.latitude,
            longitude=req.longitude,
            radius_meters=30,
            min_karma_required=0,
            is_final=False,
            branches={"branches": {}}
        )
        
        session.add(first_step)
        await session.commit()
        
        logger.info(f"✅ Создан новый квест ID={new_quest.id}: {req.title}")
        
        return {
            "status": "success",
            "id": new_quest.id,
            "title": new_quest.title,
            "message": f"✅ Квест '{req.title}' создан! ID: {new_quest.id}"
        }

@app.post("/api/admin/cities")
async def create_city(req: CreateCitySchema):
    """Создает новый город через админ-панель карты"""
    async with db.session_pool() as session:
        # Генерируем slug
        slug = generate_slug(req.name)
        
        # Проверяем, что город с таким slug еще не существует
        existing = await session.execute(select(City).where(City.slug == slug))
        if existing.first():
            raise HTTPException(
                status_code=400,
                detail=f"Город с названием '{req.name}' уже существует."
            )
        
        # Создаем новый город
        new_city = City(
            name=req.name,
            slug=slug,
            latitude=req.latitude,
            longitude=req.longitude,
            radius_km=req.radius_km,
            is_active=req.is_active,
            timezone="Asia/Yekaterinburg",
            created_at=get_utc_now()
        )
        
        session.add(new_city)
        await session.commit()
        
        logger.info(f"✅ Создан новый город ID={new_city.id}: {req.name}")
        
        return {
            "status": "success",
            "id": new_city.id,
            "name": new_city.name,
            "message": f"✅ Город '{req.name}' создан! ID: {new_city.id}"
        }


# =====================================================================
# ИГРОВОЙ ДВИЖОК: ПРОФИЛЬ, КВЕСТЫ, ЭКОНОМИКА
# =====================================================================

@app.get("/api/profile")
async def get_profile(tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    user = await db.get_user(user_id)
    if not user:
        user = await db.get_or_create_user(
            telegram_id=user_id,
            full_name=tg_user.get("first_name", "Игрок"),
            username=tg_user.get("username")
        )
        
    class_map = {"merchant": "Купец", "ranger": "Следопыт", "historian": "Историк"}
    rpg_class_ru = class_map.get(user.rpg_class, "Не выбран") if user.rpg_class else "Не выбран"
        
    xp_needed = user.level * 150
    return {
        "telegram_id": user.telegram_id,
        "full_name": user.full_name,
        "coins": user.coins,
        "karma": user.karma,
        "rpg_class": rpg_class_ru,
        "level": user.level,
        "xp": user.xp,
        "xp_needed": xp_needed,
        "max_weight_capacity": user.max_weight_capacity,
        "daily_streak": user.daily_streak,
        "global_flags": getattr(user, 'global_flags', []) or []
    }

@app.post("/api/profile/claim-income")
async def claim_income(tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    collected = await db.collect_passive_income_buffer(user_id)
    return {"status": "success", "collected_coins": collected}

@app.post("/api/profile/change-class")
async def change_rpg_class(req: ClassChangeSchema, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    success, days_left = await db.update_user_class_with_cooldown(user_id, req.rpg_class, 30)
    if not success:
        raise HTTPException(status_code=400, detail=f"Класс можно сменить через {days_left} дн.")
    return {"status": "success"}

@app.get("/api/quests")
async def list_quests(tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    user = await db.get_user(user_id)
    quests = await db.get_published_quests()
    
    result = []
    for q in quests:
        is_locked = user.level < q.min_level_required if user else True
        result.append({
            "id": q.id,
            "title": q.title,
            "description": q.description,
            "min_level_required": q.min_level_required,
            "max_speed_kmh": q.max_speed_kmh,
            "is_locked": is_locked,
            "is_coop": getattr(q, 'is_coop', False),
            "global_time_limit": getattr(q, 'global_time_limit_seconds', None)
        })
    return result

@app.get("/api/quest/active")
async def get_active_quest_state(tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active:
        return {"active": False}
        
    step = await db.get_step_by_id(active.current_step_id)
    if not step:
         return {"active": False}

    npc_dial = normalize_npc_dialogue(step.npc_dialogue)

    # Подготовка подсказок
    hints = normalize_json_field(step.hints) or []
    formatted_hints = []
    if isinstance(hints, list) and len(hints) > 0:
        for h in hints:
            formatted_hints.append({
                "text": h.get("text", ""),
                "price": h.get("price", 0),
                "delay": h.get("delay", 0) 
            })
    else:
        formatted_hints = [
            {"text": getattr(step, "hint_1_text", ""), "price": 20, "delay": getattr(step, "hint_1_delay", 5) * 60}, 
            {"text": getattr(step, "hint_2_text", ""), "price": 0, "delay": getattr(step, "hint_2_delay", 10) * 60}
        ]

    now = get_utc_now()
    last_action = active.step_activated_at if active.step_activated_at else now
    time_passed_seconds = (now - last_action).total_seconds()

    gps_verified = False
    if active.prev_time and active.step_activated_at:
        if active.prev_time > active.step_activated_at:
            gps_verified = True

    return {
        "active": True,
        "quest_id": active.quest_id,
        "score": active.score,
        "errors_count": active.errors_count,
        "is_frozen": getattr(active, 'is_frozen', False),
        "current_npc_node": getattr(active, 'current_npc_node', None),
        "gps_verified": gps_verified,
        "time_passed_seconds": time_passed_seconds,
        "step": {
            "id": step.id,
            "instruction_text": step.instruction_text,
            "history_info": step.history_info,
            "photo_then_id": step.photo_then_id,
            "photo_now_id": step.photo_now_id,
            "audio_guide_id": step.audio_guide_id,
            "latitude": step.latitude,
            "longitude": step.longitude,
            "radius_meters": getattr(step, "radius_meters", 30), # НОВОЕ: Настраиваемый радиус
            "min_karma_required": step.min_karma_required,
            "required_item": step.required_item,
            "gives_item": step.gives_item,
            "secret_price": getattr(step, "secret_price", 0),
            "npc_name": step.npc_name,
            "npc_dialogue": npc_dial,
            "hints": formatted_hints,
            "is_final": step.is_final
        }
    }

@app.post("/api/quest/start/{quest_id}")
async def start_quest(quest_id: int, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    quest = await db.get_quest_with_steps(quest_id)
    if not quest or not quest.steps:
        raise HTTPException(status_code=400, detail="Квест не содержит шагов или не существует.")
        
    first_step = quest.steps[0]
    await db.start_user_quest(user_id, quest_id, first_step.id)
    return {"status": "success", "first_step_id": first_step.id}

@app.post("/api/quest/exit")
async def exit_quest(tg_user: dict = Depends(get_current_user)):
    """Прерывает активный квест и безвозвратно удаляет сессию. Возвращает утерянные ресурсы."""
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active:
        raise HTTPException(status_code=400, detail="У вас нет активного квеста.")
        
    lost = await db.delete_active_quest(user_id)
    
    msg_parts = []
    if lost.get("coins"): msg_parts.append(f"{lost['coins']} монет")
    if lost.get("karma"): msg_parts.append(f"☯️ {lost['karma']} кармы")
    if lost.get("xp"): msg_parts.append(f"🌟 {lost['xp']} XP")
    if lost.get("items") and len(lost["items"]) > 0: 
        msg_parts.append(f"📦 Предметы: {', '.join(lost['items'])}")
    
    lost_str = "\n".join(msg_parts) if msg_parts else "Вы ничего не успели заработать."
    
    return {
        "status": "success", 
        "message": f"🛑 Квест прерван!\n\nУсловно заработанные в этой сессии ресурсы сгорели:\n{lost_str}"
    }

@app.post("/api/quest/verify-location")
async def verify_location(loc: LocationCheckSchema, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active:
        raise HTTPException(status_code=400, detail="У вас нет запущенного квеста.")
        
    step = await db.get_step_by_id(active.current_step_id)
    quest = await db.get_quest_by_id(active.quest_id)
    
    # 🌟 ЛОГИРОВАНИЕ ДЛЯ ТЕПЛОВЫХ КАРТ И РАДАРА 🌟
    async with db.session_pool() as session:
        log_entry = PlayerLocationLog(
            user_id=user_id, 
            quest_id=active.quest_id, 
            latitude=loc.latitude, 
            longitude=loc.longitude
        )
        session.add(log_entry)
        await session.commit()

    # 🌟 ПРОВЕРКА ПОГОДЫ И ВРЕМЕНИ (ЗАГЛУШКА) 🌟
    now_hour = get_utc_now().hour
    if getattr(step, 'is_night_only', False) and (6 <= now_hour <= 20):
        return {"status": "condition_failed", "message": "Эту локацию можно посетить только ночью!"}
    if getattr(step, 'is_day_only', False) and (not (6 <= now_hour <= 20)):
        return {"status": "condition_failed", "message": "Эту локацию можно посетить только днем!"}

    # 🌟 АДАПТИВНЫЙ РАДИУС И ПРОВЕРКА РАССТОЯНИЯ 🌟
    target_radius = getattr(step, "radius_meters", 30) or 30
    distance = calculate_haversine_distance(loc.latitude, loc.longitude, step.latitude, step.longitude)
    
    if distance > target_radius:
        return {
            "status": "too_far",
            "distance": int(distance),
            "message": f"Вы еще слишком далеко. До точки: {int(distance)} метров. (Необходимый радиус: {target_radius}м)"
        }
        
    # 🌟 АНТИЧИТ НА СКОРОСТЬ 🌟
    if active.prev_latitude is not None and active.prev_longitude is not None and active.prev_time is not None:
        now = get_utc_now()
        time_diff = (now - active.prev_time).total_seconds()
        if time_diff > 1.0:
            dist_prev = calculate_haversine_distance(active.prev_latitude, active.prev_longitude, loc.latitude, loc.longitude)
            speed_mps = dist_prev / time_diff
            speed_kmh = speed_mps * 3.6
            
            if speed_kmh > quest.max_speed_kmh:
                await db.add_cheat_log(user_id, quest.id, speed_mps, loc.latitude, loc.longitude)
                warnings = await db.increment_cheat_warning(user_id)
                if warnings >= 2:
                    await db.set_ban_status(user_id, True)
                    return {"status": "banned", "message": "Вы забанены античитом за использование Fake GPS!"}
                return {"status": "speed_warning", "message": f"Внимание! Превышена скорость движения: {int(speed_kmh)} км/ч!"}

    await db.set_gps_verified_now(user_id, loc.latitude, loc.longitude)

    # 🌟 АВТОЗАПУСК NPC ДИАЛОГА 🌟
    npc_started = False
    start_node = get_npc_start_node(step.npc_dialogue)
    if step.npc_name and start_node:
        await db.update_active_quest_npc_node(user_id, start_node)
        npc_started = True

    return {"status": "success", "distance": int(distance), "npc_started": npc_started}

@app.post("/api/quest/buy-hint/{hint_idx}")
async def buy_hint(hint_idx: int, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active: 
        raise HTTPException(status_code=400, detail="Нет активного квеста")
        
    step = await db.get_step_by_id(active.current_step_id)
    hints = normalize_json_field(step.hints) or []
    
    if not hints:
        hints = [
            {"price": 20, "delay": getattr(step, "hint_1_delay", 5) * 60}, 
            {"price": 0, "delay": getattr(step, "hint_2_delay", 10) * 60}
        ]

    if hint_idx < 0 or hint_idx >= len(hints): 
        raise HTTPException(status_code=400, detail="Подсказка не найдена")
        
    price = hints[hint_idx].get("price", 0)
    if price > 0:
        if not await db.spend_quest_coins(user_id, price):
            raise HTTPException(status_code=400, detail="Недостаточно монет для покупки подсказки!")
        await db.increment_error_count(user_id, score_penalty=price)
        
    return {"status": "success"}

@app.post("/api/quest/submit-answer")
async def submit_answer(ans: AnswerSubmitSchema, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active:
        raise HTTPException(status_code=400, detail="У вас нет активного квеста.")
        
    step = await db.get_step_by_id(active.current_step_id)
    user = await db.get_user(user_id)
    sys_set = await db.get_system_settings()

    user_ans = ans.answer.strip().lower()
    
    # 🌟 ОБРАБОТКА НОДОВЫХ ПЕРЕХОДОВ 🌟
    branches = normalize_json_field(step.branches) or {}
    if not isinstance(branches, dict): branches = {}
    actual_branches = branches.get("branches", branches)
    
    matched_dest = None
    for key, dest in actual_branches.items():
        if key.strip().lower() == user_ans:
            matched_dest = dest
            break
            
    if matched_dest is None:
        penalty = 20
        await db.increment_error_count(user_id, score_penalty=penalty)
        return {"status": "wrong", "message": f"❌ Неверный ответ! Штраф: -{penalty} очков."}

    # 🌟 ВЫДАЧА ГЛОБАЛЬНЫХ ФЛАГОВ И ПРЕДМЕТОВ 🌟
    earned_coins = sys_set.base_step_coins
    if user.rpg_class == "merchant": 
        earned_coins += int(earned_coins * (sys_set.merchant_bonus / 100.0))
        
    score_multiplier = sys_set.historian_mult if user.rpg_class == "historian" else 1.0
    added_score = int(sys_set.base_step_score * score_multiplier)
    
    # Обработка шанса выпадения предмета
    earned_item = step.gives_item
    drop_chance = getattr(step, 'gives_item_chance', 1.0)
    # В реале здесь генерация random.random() <= drop_chance, пока считаем 100% для примера

    await db.add_quest_rewards(user_id, coins=earned_coins, item_name=earned_item)

    # Обработка флагов (Мета-Геймплей)
    granted_flag = getattr(step, 'granted_flag', None)
    if granted_flag:
        # В db_api должен быть метод для сохранения флага:
        # await db.grant_global_flag(user_id, granted_flag)
        pass

    if str(matched_dest) == "final" or str(matched_dest) == "exit" or step.is_final:
        progress, _ = await db.finish_active_quest(user_id, int(sys_set.quest_completion_bonus * score_multiplier))
        msg = (
            f"🎉 Квест успешно пройден!\n\n"
            f"💰 Все накопленные монеты, карма и артефакты перенесены в ваш профиль!\n"
            f"🌟 Опыт: +300 XP\n"
            f"📈 Итоговые очки: {progress.score}"
        )
        return {"status": "finished", "message": msg, "score": progress.score, "errors": progress.errors_count}
        
    await db.update_active_quest_step(user_id, int(matched_dest), step.latitude, step.longitude, added_score)
    msg = f"✅ Верно! Переходим дальше.\n\nВ буфер добавлено: +{earned_coins} монет\n🎯 Очки рейтинга: +{added_score}"
    if earned_item: msg += f"\n📦 Получен артефакт: {earned_item}"

    return {"status": "next_step", "message": msg}

@app.post("/api/quest/npc-choice/{choice_index}")
async def select_npc_choice(choice_index: int, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    active = await db.get_active_quest(user_id)
    if not active or not getattr(active, 'current_npc_node', None):
        raise HTTPException(status_code=400, detail="Диалог с NPC не запущен.")
        
    step = await db.get_step_by_id(active.current_step_id)
    dialogue = normalize_npc_dialogue(step.npc_dialogue)
    if dialogue is None:
        raise HTTPException(status_code=400, detail="Диалог с NPC недоступен.")

    node = dialogue.get(active.current_npc_node)
    if node is None:
        fallback_node = get_npc_start_node(dialogue)
        if fallback_node is None: raise HTTPException(status_code=400, detail="Ни один узел диалога не найден.")
        node = dialogue.get(fallback_node)
        await db.update_active_quest_npc_node(user_id, fallback_node)

    options = node.get("options", [])
    if choice_index < 0 or choice_index >= len(options):
         raise HTTPException(status_code=400, detail="Неверный выбор.")
         
    opt = options[choice_index]
    msg_parts = []
    k_change = opt.get("karma_change", 0)
    c_change = opt.get("coins_change", 0)
    
    if k_change != 0:
        await db.add_quest_rewards(user_id, karma=k_change)
        msg_parts.append(f"☯️ Карма (в буфер): {'+' if k_change > 0 else ''}{k_change}")
        
    if c_change > 0:
        await db.add_quest_rewards(user_id, coins=c_change)
        msg_parts.append(f"В буфер: +{c_change} монет")
    elif c_change < 0:
        spent = await db.spend_quest_coins(user_id, abs(c_change))
        if not spent: raise HTTPException(status_code=400, detail="Недостаточно монет для этого выбора!")
        msg_parts.append(f"💸 Потрачено: {abs(c_change)} монет")

    reward_str = "\n".join(msg_parts)
    next_node = opt.get("next_node", "exit")
    
    if next_node == "exit":
        await db.update_active_quest_npc_node(user_id, None)
        final_msg = "Диалог завершен. Теперь решите загадку локации!"
        if reward_str: final_msg += f"\n\n{reward_str}"
        return {"status": "exit", "message": final_msg}
        
    await db.update_active_quest_npc_node(user_id, next_node)
    final_msg = "Диалог обновлен."
    if reward_str: final_msg += f"\n\n{reward_str}"
    return {"status": "next_node", "node": next_node, "message": final_msg}

@app.get("/api/inventory")
async def get_inventory(tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    items = await db.get_user_inventory(user_id)
    weight = await db.get_user_current_weight(user_id)
    return {"items": items, "current_weight": weight}

@app.post("/api/inventory/use/{item_name}")
async def use_item(item_name: str, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    success, msg = await db.activate_consumable_item(user_id, item_name)
    if not success:
         raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}

@app.post("/api/inventory/discard/{item_name}")
async def discard_item(item_name: str, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    success = await db.discard_inventory_item(user_id, item_name)
    if not success:
        raise HTTPException(status_code=400, detail="Предмет не найден.")
    return {"status": "success"}

@app.get("/api/shop")
async def get_shop_catalog(tg_user: dict = Depends(get_current_user)):
    items = await db.get_shop_items()
    catalog = [i for i in items if getattr(i, 'market_id', None) is None]
    return [
        {
            "id": i.id,
            "name": i.name,
            "description": i.description,
            "price": i.price,
            "item_name": i.item_name,
            "item_type": i.item_type,
            "weight": getattr(i, 'weight', 0),
            "generates_income": getattr(i, 'generates_income', False),
            "income_per_hour": getattr(i, 'income_per_hour', 0)
        } for i in catalog
    ]

@app.post("/api/shop/buy/{item_id}")
async def buy_item(item_id: int, tg_user: dict = Depends(get_current_user)):
    user_id = tg_user.get("id")
    shop_item = await db.get_shop_item_by_id(item_id)
    if not shop_item:
        raise HTTPException(status_code=404, detail="Товар не найден.")
        
    user = await db.get_user(user_id)
    if user.coins < shop_item.price:
        raise HTTPException(status_code=400, detail="Недостаточно монет.")
        
    overloaded, _, _ = await db.is_inventory_overloaded(user_id, getattr(shop_item, 'weight', 0))
    if overloaded:
        raise HTTPException(status_code=400, detail="Рюкзак перегружен! Выбросите лишние вещи.")
        
    await db.deduct_coins(user_id, shop_item.price)
    await db.add_item_to_inventory(user_id, shop_item.item_name)
    return {"status": "success", "item_name": shop_item.name}

@app.get("/api/leaderboard")
async def get_leaderboard_data(period: str = "global", tg_user: dict = Depends(get_current_user)):
    if period == "global":
        data = await db.get_leaderboard(limit=15)
    else:
        data = await db.get_seasonal_leaderboard(period=period, limit=15)
    return data
