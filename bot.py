# === БЛОК ИМПОРТОВ ===
import logging
import os
import random
import requests
import uuid
from urllib.parse import urlparse # Для обработки URL от Railway

from telegram import Update, InlineQueryResultPhoto
from telegram.constants import ChatAction
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ БОТА (из переменных окружения) ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID")
IS_BOT_ENABLED_STR = os.environ.get("BOT_ENABLED", "true").lower() # По умолчанию включен
IS_BOT_ENABLED = IS_BOT_ENABLED_STR == "true"

# Railway предоставляет порт через переменную окружения PORT
PORT = int(os.environ.get('PORT', '8443')) # 8443 - запасной вариант, если PORT не установлен (маловероятно на Railway)

# Railway может предоставлять публичный домен через разные переменные
# Проверьте ваш дашборд Railway или их документацию.
# Частые варианты: RAILWAY_PUBLIC_DOMAIN, RAILWAY_STATIC_URL
# Если переменная содержит полный URL (https://...), мы извлечем только хост.
RAILWAY_GENERATED_DOMAIN_FULL = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or \
                                os.environ.get("RAILWAY_STATIC_URL") or \
                                os.environ.get("RAILWAY_URL") # Еще один возможный вариант

RAILWAY_HOST_DOMAIN = None
if RAILWAY_GENERATED_DOMAIN_FULL:
    if RAILWAY_GENERATED_DOMAIN_FULL.startswith("http"):
        parsed_url = urlparse(RAILWAY_GENERATED_DOMAIN_FULL)
        RAILWAY_HOST_DOMAIN = parsed_url.netloc # example.up.railway.app
    else:
        RAILWAY_HOST_DOMAIN = RAILWAY_GENERATED_DOMAIN_FULL # Если уже только хост

# === ФУНКЦИЯ ПОИСКА ИЗОБРАЖЕНИЙ ===
def search_images_data(query: str) -> list[dict]:
    logger.info(f"Поиск изображений по запросу: {query}")
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': GOOGLE_API_KEY,
        'cx': GOOGLE_CSE_ID,
        'searchType': 'image',
        'num': 10,
        'safe': 'off'
    }
    image_data_list = []
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
        logger.info(f"Найдено {len(image_data_list)} изображений с превью для запроса: {query}")
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка: {http_err} - {response.text if 'response' in locals() and response else 'No response text'}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при вызове Google API: {e}")
    except Exception as e:
        logger.error(f"Другая ошибка при поиске изображений: {e}")
    return image_data_list

# === ОБРАБОТЧИКИ КОМАНД TELEGRAM ===
async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    bot_username = context.bot.username
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
    images_data = search_images_data(query)

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
            await update.message.reply_text(f"Не удалось отправить картинку. Возможно, ссылка некорректна или Telegram не смог ее обработать.")
    else:
        await update.message.reply_text(f'К сожалению, ничего не найдено по запросу "{query}" или произошла ошибка API.')

# === ОБРАБОТЧИК INLINE-ЗАПРОСОВ ===
async def inline_query_handler(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    if not query:
        return

    logger.info(f"Inline-запрос получен: '{query}'")
    images_data = search_images_data(query)
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

    try:
        await update.inline_query.answer(results[:20], cache_time=300)
        logger.info(f"Отправлено {len(results[:20])} inline-результатов для запроса '{query}'")
    except Exception as e:
        logger.error(f"Ошибка ответа на inline-запрос: {e}")

# === ОБРАБОТЧИК ОШИБОК ===
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)

# === ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА БОТА (main) ===
def main() -> None:
    if not IS_BOT_ENABLED:
        logger.info("Бот отключен через переменную окружения BOT_ENABLED. Завершение работы.")
        return
    # Проверка наличия всех необходимых токенов
    if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, GOOGLE_CSE_ID]):
        logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Не установлены одна или несколько обязательных переменных окружения: TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, GOOGLE_CSE_ID.")
        return

    application_builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN)
    application = application_builder.build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, image_search_handler))
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_error_handler(error_handler)

    # Определяем, запускать с вебхуком (для Railway) или с long polling (если домен Railway не определен)
    run_with_webhook = bool(RAILWAY_HOST_DOMAIN) and bool(os.environ.get("PORT"))

    if run_with_webhook:
        if not RAILWAY_HOST_DOMAIN: # Дополнительная проверка
            logger.error("Домен Railway (RAILWAY_HOST_DOMAIN) не определен, не могу запустить вебхук.")
            logger.info("Попытка запуска с long polling как запасной вариант...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            return

        webhook_url = f"https://{RAILWAY_HOST_DOMAIN}/{TELEGRAM_BOT_TOKEN}"
        logger.info(f"Запуск бота с вебхуком на Railway. Установка вебхука на: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        logger.warning("Домен Railway не определен (проверьте переменные RAILWAY_PUBLIC_DOMAIN, RAILWAY_STATIC_URL и т.п.).")
        logger.info("Запуск бота с long polling (может не работать корректно на платформах без постоянного процесса)...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()