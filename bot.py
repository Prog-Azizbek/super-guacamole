# === БЛОК ИМПОРТОВ ===
import logging
# from dotenv import load_dotenv
# load_dotenv()
import os
import random
import requests
import uuid
from urllib.parse import urlparse

from telegram import Update, InlineQueryResultPhoto
from telegram.constants import ChatAction
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ БОТА ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID")
PORT = int(os.environ.get('PORT', '8443'))
RAILWAY_GENERATED_DOMAIN_FULL = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or \
                                os.environ.get("RAILWAY_STATIC_URL") or \
                                os.environ.get("RAILWAY_URL")
RAILWAY_HOST_DOMAIN = None
if RAILWAY_GENERATED_DOMAIN_FULL:
    if RAILWAY_GENERATED_DOMAIN_FULL.startswith("http"):
        parsed_url = urlparse(RAILWAY_GENERATED_DOMAIN_FULL)
        RAILWAY_HOST_DOMAIN = parsed_url.netloc
    else:
        RAILWAY_HOST_DOMAIN = RAILWAY_GENERATED_DOMAIN_FULL
IS_BOT_ENABLED_STR = os.environ.get("BOT_ENABLED", "true").lower()
IS_BOT_ENABLED = IS_BOT_ENABLED_STR == "true"

# === КОНСТАНТЫ ===
RESULTS_PER_PAGE = 10 # Сколько результатов запрашивать у Google API за раз (макс. 10)
MAX_INLINE_RESULTS_TOTAL = 30 # Максимальное общее количество результатов для inline (3 запроса к Google)

# === ФУНКЦИЯ ПОИСКА ИЗОБРАЖЕНИЙ С ПАГИНАЦИЕЙ ===
def search_images_paginated(query: str, start_index: int = 1) -> dict:
    logger.info(f"Поиск изображений по запросу: '{query}', начиная с индекса: {start_index}")
    # Если GOOGLE_API_KEY или GOOGLE_CSE_ID не установлены, не делаем запрос
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        logger.error("GOOGLE_API_KEY или GOOGLE_CSE_ID не установлены. Поиск невозможен.")
        return {'images': [], 'next_start_index': None}
        
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': GOOGLE_API_KEY,
        'cx': GOOGLE_CSE_ID,
        'searchType': 'image',
        'num': RESULTS_PER_PAGE,
        'start': start_index,
        'safe': 'off'
    }
    image_data_list = []
    next_page_start_index = None

    try:
        response = requests.get(search_url, params=params, timeout=20)
        response.raise_for_status()
        results_json = response.json()

        if 'items' in results_json:
            for item in results_json['items']:
                if 'link' in item and 'image' in item and 'thumbnailLink' in item['image']:
                    image_data_list.append({
                        'id': str(uuid.uuid4()),
                        'photo_url': item['link'],
                        'thumbnail_url': item['image']['thumbnailLink'],
                    })
        
        logger.info(f"Найдено {len(image_data_list)} изображений для текущей страницы (запрос: '{query}', start: {start_index})")

        if 'queries' in results_json and 'nextPage' in results_json['queries']:
            if results_json['queries']['nextPage'] and len(results_json['queries']['nextPage']) > 0:
                potential_next_start = results_json['queries']['nextPage'][0].get('startIndex')
                if potential_next_start and potential_next_start <= MAX_INLINE_RESULTS_TOTAL:
                     next_page_start_index = potential_next_start

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка при поиске: {http_err} - {response.text if 'response' in locals() and response else 'No response text'}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при поиске: {e}")
    except Exception as e:
        logger.error(f"Другая ошибка при поиске изображений: {e}")
    
    return {'images': image_data_list, 'next_start_index': next_page_start_index}

# === ОБРАБОТЧИКИ КОМАНД TELEGRAM ===
async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    bot_username = context.bot.username if context.bot else "имя_бота" # Запасной вариант
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Отправь мне поисковый запрос, или используй меня в inline-режиме: @{bot_username} <запрос>",
    )

async def image_search_handler(update: Update, context: CallbackContext) -> None:
    query = update.message.text
    if not query:
        await update.message.reply_text("Пожалуйста, введите поисковый запрос.")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    
    search_result = search_images_paginated(query, start_index=1) 
    images_data = search_result.get('images', [])

    if images_data:
        try:
            random_image_data = random.choice(images_data)
            logger.info(f"Отправка изображения: {random_image_data['photo_url']} по запросу: {query}")
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=random_image_data['photo_url'],
                caption=f"Результат по запросу: {query}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}. URL: {random_image_data.get('photo_url', 'N/A') if 'random_image_data' in locals() else 'N/A'}")
            await update.message.reply_text(f"Не удалось отправить картинку. Попробуйте другой результат или запрос.")
    else:
        await update.message.reply_text(f'К сожалению, ничего не найдено по запросу "{query}" или произошла ошибка API.')

# === ОБРАБОТЧИК INLINE-ЗАПРОСОВ ===
async def inline_query_handler(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query.strip() # Убираем лишние пробелы
    offset = update.inline_query.offset

    current_start_index = 1
    if offset:
        try:
            current_start_index = int(offset)
            if current_start_index <= 0: # Защита от некорректного offset
                 current_start_index = 1
        except ValueError:
            logger.warning(f"Некорректный offset: {offset}. Используем start_index=1.")
            current_start_index = 1
    
    # Если запрос пустой и это не запрос следующей страницы, ничего не делаем
    # Например, когда пользователь только открыл @имя_бота и еще ничего не ввел
    if not query and not offset:
        # Можно отправить пустой ответ с подсказкой, если Telegram это поддерживает для пустого запроса
        # await update.inline_query.answer([], switch_pm_text="Введите запрос для поиска", switch_pm_parameter="help_inline")
        # Пока просто ничего не делаем
        logger.debug("Пустой inline-запрос без offset, ничего не делаем.")
        return
    
    # Если запрос пустой, но есть offset (пользователь скроллит старые результаты для пустого запроса)
    if not query and offset:
        await update.inline_query.answer([], next_offset="") # Больше нет результатов для пустого запроса
        return

    logger.info(f"Inline-запрос: '{query}', offset: '{offset}', start_index: {current_start_index}")

    if current_start_index > MAX_INLINE_RESULTS_TOTAL:
        logger.info(f"Достигнут MAX_INLINE_RESULTS_TOTAL ({MAX_INLINE_RESULTS_TOTAL}) для '{query}'.")
        await update.inline_query.answer([], next_offset="")
        return

    search_result = search_images_paginated(query, start_index=current_start_index)
    images_data = search_result.get('images', [])
    next_page_start_index = search_result.get('next_start_index')

    results = []
    for img_data in images_data:
        try:
            results.append(
                InlineQueryResultPhoto(
                    id=img_data['id'],
                    photo_url=img_data['photo_url'],
                    thumbnail_url=img_data['thumbnail_url'],
                )
            )
        except Exception as e:
            logger.error(f"Ошибка создания InlineQueryResultPhoto: {e} для ID: {img_data.get('id')}")

    next_offset_value = ""
    if next_page_start_index:
        next_offset_value = str(next_page_start_index)

    # Настройка времени кэширования
    if not query: # Если запрос в итоге пустой (хотя мы выше это отсекаем)
        cache_time_val = 60
    elif not offset: # Первый запрос для данного query
        cache_time_val = 3600  # 1 час
    else: # Последующие страницы пагинации
        cache_time_val = 300   # 5 минут
        
    try:
        await update.inline_query.answer(
            results, 
            cache_time=cache_time_val, 
            next_offset=next_offset_value,
            # is_personal=False # Результаты не персональные, могут кэшироваться для всех
        )
        logger.info(f"Отправлено {len(results)} inline-результатов для '{query}', next_offset: '{next_offset_value}', cache: {cache_time_val}s")
    except Exception as e:
        logger.error(f"Ошибка ответа на inline-запрос: {e}")

# === ОБРАБОТЧИК ОШИБОК ===
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.warning('Update "%s" вызвал ошибку "%s"', update, context.error, exc_info=context.error)

# === ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА БОТА ===
def main() -> None:
    if not IS_BOT_ENABLED:
        logger.info("Бот отключен через переменную окружения BOT_ENABLED=false. Завершение работы.")
        return

    if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, GOOGLE_CSE_ID]):
        logger.critical("ОШИБКА ЗАПУСКА: Не установлены одна или несколько обязательных переменных окружения: TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, GOOGLE_CSE_ID.")
        return
    
    logger.info(f"Ключи API Google: KEY установлен - {'Да' if GOOGLE_API_KEY else 'Нет'}, CSE_ID установлен - {'Да' if GOOGLE_CSE_ID else 'Нет'}")


    application_builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN)
    application = application_builder.build()

    # logger.info(f"Имя бота: {application.bot.username}") # Это можно сделать после сборки application

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, image_search_handler))
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_error_handler(error_handler)

    run_with_webhook = bool(RAILWAY_HOST_DOMAIN) and bool(os.environ.get("PORT"))

    if run_with_webhook:
        if not RAILWAY_HOST_DOMAIN:
            logger.error("Домен Railway (RAILWAY_HOST_DOMAIN) не определен. Вебхук не может быть запущен.")
            logger.info("Попытка запуска с long polling как запасной вариант...")
            application.run_polling(allowed_updates=Update.ALL_TYPES) # Явно разрешаем все типы
            return
        webhook_url = f"https://{RAILWAY_HOST_DOMAIN}/{TELEGRAM_BOT_TOKEN}"
        logger.info(f"Бот включен. Запуск с вебхуком. Установка вебхука на: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES # Явно разрешаем все типы обновлений для вебхука
        )
    else:
        logger.warning("Домен Railway не определен или PORT не установлен. Проверьте переменные окружения (RAILWAY_PUBLIC_DOMAIN, и т.п.).")
        logger.info("Бот включен. Запуск с long polling.")
        application.run_polling(allowed_updates=Update.ALL_TYPES) # Явно разрешаем все типы

if __name__ == '__main__':
    main()