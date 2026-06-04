import os
import logging as _log
from typing import Any

from sqladmin import Admin, ModelView, action
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.datastructures import UploadFile
from aiogram import Bot
from aiogram.types import BufferedInputFile
from wtforms.fields import FileField as WTFileField, TextAreaField

from tgbot.config import settings
from tgbot.database.models import (
    User, Quest, Step, ActiveQuest, QuestProgress,
    InventoryItem, Achievement, UserAchievement,
    ShopItem, PromoCode, DailyRiddle, CheatLog,
    ScheduledBroadcast, QuestMarket, RandomEvent,
    GlobalEvent, SystemSettings, City, Season,
    CoopSession, CoopMember, PvPDuel, PvPQuestion,
    Guild, GuildMember, CraftRecipe, Challenge,
    UserChallenge, QuestReview, PhotoReport,
    SupportTicket, GemTransaction, ARMarker, ARScanLog,
    NPCCharacter, Sponsor, PlayerLocationLog
)

ADMIN_LOGIN = os.environ.get("ADMIN_PANEL_LOGIN", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "changeme_2026")
ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "super-secret-key-change-in-production")

_log.getLogger(__name__).info(f"Admin panel configured: login='{ADMIN_LOGIN}'")

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        if form.get("username") == ADMIN_LOGIN and form.get("password") == ADMIN_PASSWORD:
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)

async def upload_file_to_telegram(file: UploadFile, is_audio: bool = False) -> str | None:
    if not file or not getattr(file, "filename", None):
        return None
    bot = Bot(token=settings.bot.token.get_secret_value())
    input_file = BufferedInputFile(await file.read(), filename=file.filename)
    try:
        if is_audio:
            msg = await bot.send_audio(chat_id=settings.bot.dump_channel_id, audio=input_file)
            return msg.audio.file_id
        else:
            msg = await bot.send_photo(chat_id=settings.bot.dump_channel_id, photo=input_file)
            return msg.photo[-1].file_id
    except Exception as e:
        _log.error(f"Telegram API Upload Error: {e}")
        return None
    finally:
        await bot.session.close()

class SafeFileField(WTFileField):
    def process_data(self, value):
        self.data = None if isinstance(value, str) else value
        if not isinstance(value, str):
            super().process_data(value)

# =====================================================================
# ВЬЮШКИ: ИГРОКИ И СОЦИАЛКА
# =====================================================================
class UserAdmin(ModelView, model=User):
    category = "👥 Игроки и Социалка"
    name = "Игрок"
    name_plural = "Игроки"
    icon = "fa-solid fa-users"
    column_list = ["telegram_id", "full_name", "username", "level", "xp", "coins", "karma", "rpg_class", "is_banned"]
    column_searchable_list = ["full_name", "username", "telegram_id"]

class GuildAdmin(ModelView, model=Guild):
    category = "👥 Игроки и Социалка"
    name = "Гильдия"
    name_plural = "Гильдии"
    icon = "fa-solid fa-shield-halved"
    column_list = ["id", "name", "leader_id", "city_id", "level", "total_xp", "max_members"]

class GuildMemberAdmin(ModelView, model=GuildMember):
    category = "👥 Игроки и Социалка"
    name = "Участник гильдии"
    name_plural = "Участники гильдий"
    icon = "fa-solid fa-user-shield"
    column_list = ["id", "guild_id", "user_id", "role", "contribution_xp", "joined_at"]

class PvPDuelAdmin(ModelView, model=PvPDuel):
    category = "👥 Игроки и Социалка"
    name = "PvP Дуэль"
    name_plural = "PvP Дуэли"
    icon = "fa-solid fa-swords"
    column_list = ["id", "challenger_id", "opponent_id", "status", "bet_coins"]

class PvPQuestionAdmin(ModelView, model=PvPQuestion):
    category = "👥 Игроки и Социалка"
    name = "Вопрос PvP"
    name_plural = "Вопросы PvP"
    icon = "fa-solid fa-circle-question"
    column_list = ["id", "question", "correct_answer", "difficulty"]

class CoopSessionAdmin(ModelView, model=CoopSession):
    category = "👥 Игроки и Социалка"
    name = "Кооп сессия"
    name_plural = "Кооп сессии"
    icon = "fa-solid fa-people-group"
    column_list = ["id", "quest_id", "leader_id", "invite_code", "is_active"]

# =====================================================================
# ВЬЮШКИ: ЛОКАЦИИ, КВЕСТЫ, СЮЖЕТ
# =====================================================================
class CityAdmin(ModelView, model=City):
    category = "🗺 Локации и Квесты"
    name = "Город"
    name_plural = "Города"
    icon = "fa-solid fa-city"
    column_list = ["id", "name", "slug", "latitude", "longitude", "radius_km", "is_active"]

class QuestAdmin(ModelView, model=Quest):
    category = "🗺 Локации и Квесты"
    name = "Квест"
    name_plural = "Квесты"
    icon = "fa-solid fa-map-location-dot"
    column_list = ["id", "title", "is_published", "city_id", "global_time_limit_seconds", "is_coop"]
    form_args = {
        "global_time_limit_seconds": {"label": "Глобальный таймер (сек)"},
        "coop_max_size": {"label": "Размер команды (макс)"}
    }

class StepAdmin(ModelView, model=Step):
    category = "🗺 Локации и Квесты"
    name = "Узел/Шаг квеста"
    name_plural = "Граф шагов"
    icon = "fa-solid fa-project-diagram"
    column_list = ["id", "quest", "instruction_text", "latitude", "longitude", "radius_meters", "is_final"]
    column_searchable_list = ["instruction_text", "npc_name"]
    form_columns = [
        "quest", "instruction_text", "branches", "is_final", "hints", "history_info",
        "photo_then_id", "photo_now_id", "audio_guide_id", "latitude", "longitude", "radius_meters",
        "weather_sun_only", "weather_rain_only", "weather_snow_only", "is_night_only", "is_day_only",
        "sponsor_id", "required_flag", "granted_flag", "time_limit_seconds",
        "npc_name", "npc_dialogue", "min_karma_required", "required_item", "gives_item", "gives_item_chance", "secret_price"
    ]
    form_overrides = {
        "photo_then_id": SafeFileField, "photo_now_id": SafeFileField, "audio_guide_id": SafeFileField,
        "branches": TextAreaField, "hints": TextAreaField, "npc_dialogue": TextAreaField,
    }
    form_args = {
        "branches": {"label": "🧠 ЛОГИКА ГРАФА И ПЕРЕХОДЫ", "render_kw": {"style": "display:none;"}},
        "npc_dialogue": {"label": "Сценарий диалога", "render_kw": {"style": "display:none;"}},
        "hints": {"label": "Пул подсказок", "render_kw": {"style": "display:none;"}},
        "radius_meters": {"label": "Радиус триггера (м)"},
        "gives_item_chance": {"label": "Шанс выпадения предмета (0.0 - 1.0)"}
    }
    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        for key in ["photo_then_id", "photo_now_id", "audio_guide_id"]:
            if key in data and isinstance(data[key], UploadFile) and data[key].filename:
                is_audio = (key == "audio_guide_id")
                data[key] = await upload_file_to_telegram(data[key], is_audio=is_audio)
            elif not is_created and hasattr(model, key):
                data[key] = getattr(model, key)

class SeasonAdmin(ModelView, model=Season):
    category = "🗺 Локации и Квесты"
    name = "Сезон"
    name_plural = "Сезоны"
    icon = "fa-solid fa-calendar-days"
    column_list = ["id", "name", "city_id", "starts_at", "ends_at", "is_active"]

class QuestMarketAdmin(ModelView, model=QuestMarket):
    category = "🗺 Локации и Квесты"
    name = "Торговая лавка"
    name_plural = "Торговые лавки"
    icon = "fa-solid fa-store"
    column_list = ["id", "name", "latitude", "longitude", "radius"]

class NPCCharacterAdmin(ModelView, model=NPCCharacter):
    category = "🎭 Сюжет и NPC"
    name = "Персонаж (NPC)"
    name_plural = "База NPC"
    icon = "fa-solid fa-user-astronaut"
    column_list = ["id", "name", "description"]
    form_overrides = {"avatar_id": SafeFileField}
    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if "avatar_id" in data and isinstance(data["avatar_id"], UploadFile):
            data["avatar_id"] = await upload_file_to_telegram(data["avatar_id"])

# =====================================================================
# ВЬЮШКИ: ИГРОВОЙ ПРОЦЕСС
# =====================================================================
class ActiveQuestAdmin(ModelView, model=ActiveQuest):
    category = "⚔️ Игровой процесс"
    name = "Активная сессия"
    name_plural = "Активные сессии"
    icon = "fa-solid fa-play"
    column_list = ["user_id", "quest_id", "current_step_id", "score", "is_suspended", "is_frozen"]

class QuestProgressAdmin(ModelView, model=QuestProgress):
    category = "⚔️ Игровой процесс"
    name = "Прохождение"
    name_plural = "Прохождения"
    icon = "fa-solid fa-trophy"
    column_list = ["id", "user_id", "quest_id", "score", "total_time_seconds", "completed_at"]
    column_default_sort = ("completed_at", True)

class ChallengeAdmin(ModelView, model=Challenge):
    category = "⚔️ Игровой процесс"
    name = "Челлендж"
    name_plural = "Челленджи"
    icon = "fa-solid fa-bullseye"
    column_list = ["id", "title", "challenge_type", "target_value", "reward_coins", "is_active"]

class RandomEventAdmin(ModelView, model=RandomEvent):
    category = "⚔️ Игровой процесс"
    name = "Случайное событие"
    name_plural = "Случайные события"
    icon = "fa-solid fa-dice"
    column_list = ["id", "event_type", "probability", "coins_impact", "karma_impact"]

class GlobalEventAdmin(ModelView, model=GlobalEvent):
    category = "⚔️ Игровой процесс"
    name = "Глобальный ивент"
    name_plural = "Глобальные ивенты"
    icon = "fa-solid fa-globe"
    column_list = ["id", "name", "city_id", "is_active", "started_at"]

# =====================================================================
# ВЬЮШКИ: ЭКОНОМИКА И ВЕЩИ
# =====================================================================
class InventoryItemAdmin(ModelView, model=InventoryItem):
    category = "🎒 Экономика и Вещи"
    name = "Предмет в инвентаре"
    name_plural = "Инвентарь игроков"
    icon = "fa-solid fa-box"
    column_list = ["id", "user_id", "item_name", "weight", "is_consumable", "generates_income"]

class ShopItemAdmin(ModelView, model=ShopItem):
    category = "🎒 Экономика и Вещи"
    name = "Товар"
    name_plural = "Магазин"
    icon = "fa-solid fa-shop"
    column_list = ["id", "name", "price", "item_type", "weight", "generates_income", "market_id"]

class CraftRecipeAdmin(ModelView, model=CraftRecipe):
    category = "🎒 Экономика и Вещи"
    name = "Рецепт крафта"
    name_plural = "Рецепты крафта"
    icon = "fa-solid fa-hammer"
    column_list = ["id", "name", "result_item_name", "coins_cost", "min_level"]

class GemTransactionAdmin(ModelView, model=GemTransaction):
    category = "🎒 Экономика и Вещи"
    name = "Транзакция гемов"
    name_plural = "Транзакции гемов"
    icon = "fa-solid fa-gem"
    can_create = False
    can_edit = False
    column_list = ["id", "user_id", "amount", "transaction_type", "description", "created_at"]

# =====================================================================
# ВЬЮШКИ: AR, КОНТЕНТ И ОТВЕТЫ
# =====================================================================
class ARMarkerAdmin(ModelView, model=ARMarker):
    category = "🕶 Дополненная реальность"
    name = "AR-Маркер"
    name_plural = "AR-Маркеры (Image Tracking)"
    icon = "fa-solid fa-vr-cardboard"
    column_list = ["id", "code", "name", "is_shard_collection", "is_active"]
    form_overrides = {"image_ref_id": SafeFileField}
    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if "image_ref_id" in data and isinstance(data["image_ref_id"], UploadFile):
            data["image_ref_id"] = await upload_file_to_telegram(data["image_ref_id"])

class AchievementAdmin(ModelView, model=Achievement):
    category = "🏆 Контент и Ответы"
    name = "Достижение"
    name_plural = "Достижения"
    icon = "fa-solid fa-medal"
    column_list = ["id", "name", "badge_emoji", "required_action", "reward_coins"]

class DailyRiddleAdmin(ModelView, model=DailyRiddle):
    category = "🏆 Контент и Ответы"
    name = "Загадка дня"
    name_plural = "Загадки дня"
    icon = "fa-solid fa-puzzle-piece"
    column_list = ["id", "question", "correct_answer", "reward_coins"]

class QuestReviewAdmin(ModelView, model=QuestReview):
    category = "🏆 Контент и Ответы"
    name = "Отзыв"
    name_plural = "Отзывы на квесты"
    icon = "fa-solid fa-star"
    column_list = ["id", "user_id", "quest_id", "rating", "created_at"]

class PhotoReportAdmin(ModelView, model=PhotoReport):
    category = "🏆 Контент и Ответы"
    name = "Фото-отчёт"
    name_plural = "Фото-отчёты"
    icon = "fa-solid fa-camera"
    column_list = ["id", "user_id", "quest_id", "created_at"]

# =====================================================================
# ВЬЮШКИ: LIVE-OPS, СИСТЕМА И ЛОГИ
# =====================================================================
class SponsorAdmin(ModelView, model=Sponsor):
    category = "💰 Live-Ops и Монетизация"
    name = "Спонсор"
    name_plural = "Спонсоры локаций"
    icon = "fa-solid fa-handshake"
    column_list = ["id", "name", "active"]
    form_overrides = {"logo_id": SafeFileField}
    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if "logo_id" in data and isinstance(data["logo_id"], UploadFile):
            data["logo_id"] = await upload_file_to_telegram(data["logo_id"])

class PlayerLocationLogAdmin(ModelView, model=PlayerLocationLog):
    category = "📊 Аналитика и Heatmaps"
    name = "Лог геолокации"
    name_plural = "Радар игроков"
    icon = "fa-solid fa-satellite-dish"
    can_create = False
    can_edit = False
    column_list = ["user_id", "quest_id", "latitude", "longitude", "timestamp"]
    column_default_sort = ("timestamp", True)

class CheatLogAdmin(ModelView, model=CheatLog):
    category = "⚙️ Система и Логи"
    name = "Лог античета"
    name_plural = "Логи античета"
    icon = "fa-solid fa-triangle-exclamation"
    can_create = False
    can_edit = False
    column_list = ["id", "user_id", "quest_id", "speed", "created_at"]
    column_default_sort = ("created_at", True)

class ScheduledBroadcastAdmin(ModelView, model=ScheduledBroadcast):
    category = "⚙️ Система и Логи"
    name = "Рассылка"
    name_plural = "Рассылки"
    icon = "fa-solid fa-paper-plane"
    column_list = ["id", "text", "send_at", "is_sent"]

class SupportTicketAdmin(ModelView, model=SupportTicket):
    category = "⚙️ Система и Логи"
    name = "Тикет поддержки"
    name_plural = "Тикеты поддержки"
    icon = "fa-solid fa-headset"
    column_list = ["id", "user_id", "subject", "status", "created_at"]

class SystemSettingsAdmin(ModelView, model=SystemSettings):
    category = "⚙️ Система и Логи"
    name = "Настройки"
    name_plural = "Системные настройки"
    icon = "fa-solid fa-gear"
    can_create = False
    can_delete = False
    column_list = ["id", "tutorial_answer", "merchant_bonus", "base_step_coins"]

# =====================================================================
# ИНИЦИАЛИЗАЦИЯ
# =====================================================================
def setup_admin(app, engine):
    import os as _os
    templates_dir = _os.path.join(_os.path.dirname(__file__), "templates")
    admin = Admin(
        app=app, engine=engine,
        authentication_backend=AdminAuth(secret_key=ADMIN_SECRET_KEY),
        title="🗺 Quest Sity — Игровой Движок", base_url="/admin", templates_dir=templates_dir
    )
    
    # 1. Игроки и Социалка
    admin.add_view(UserAdmin)
    admin.add_view(GuildAdmin)
    admin.add_view(GuildMemberAdmin)
    admin.add_view(PvPDuelAdmin)
    admin.add_view(PvPQuestionAdmin)
    admin.add_view(CoopSessionAdmin)
    
    # 2. Локации и Квесты
    admin.add_view(CityAdmin)
    admin.add_view(QuestAdmin)
    admin.add_view(StepAdmin)
    admin.add_view(SeasonAdmin)
    admin.add_view(QuestMarketAdmin)
    
    # 3. Сюжет и NPC
    admin.add_view(NPCCharacterAdmin)
    
    # 4. Игровой процесс
    admin.add_view(ActiveQuestAdmin)
    admin.add_view(QuestProgressAdmin)
    admin.add_view(ChallengeAdmin)
    admin.add_view(RandomEventAdmin)
    admin.add_view(GlobalEventAdmin)
    
    # 5. Экономика
    admin.add_view(InventoryItemAdmin)
    admin.add_view(ShopItemAdmin)
    admin.add_view(CraftRecipeAdmin)
    admin.add_view(GemTransactionAdmin)
    
    # 6. AR, Контент
    admin.add_view(ARMarkerAdmin)
    admin.add_view(AchievementAdmin)
    admin.add_view(DailyRiddleAdmin)
    admin.add_view(QuestReviewAdmin)
    admin.add_view(PhotoReportAdmin)
    
    # 7. Live-Ops, Аналитика, Логи
    admin.add_view(SponsorAdmin)
    admin.add_view(PlayerLocationLogAdmin)
    admin.add_view(CheatLogAdmin)
    admin.add_view(ScheduledBroadcastAdmin)
    admin.add_view(SupportTicketAdmin)
    admin.add_view(SystemSettingsAdmin)

    return admin