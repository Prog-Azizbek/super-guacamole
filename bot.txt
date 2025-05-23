import logging
from dotenv import load_dotenv # Раскомментируй для локального запуска, если есть .env файл
load_dotenv() # Раскомментируй для локального запуска

import os
import random
import requests
import uuid # Для генерации уникальных ID для inline-результатов

from telegram import Update, InlineQueryResultPhoto
from telegram.constants import ChatAction
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext

# Включаем логирование для отладки (особенно на Heroku)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID")
HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME")

PORT = int(os.environ.get('PORT', '8443'))

# --- ФУНКЦИЯ ПОИСКА ИЗОБРАЖЕНИЙ (модифицирована для возврата данных с превью) ---
def search_images_data(query: str) -> list[dict]:
    logger.info(f"Поиск изображений по запросу: {query}")
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': GOOGLE_API_KEY,
        'cx': GOOGLE_CSE_ID,
        'searchType': 'image',
        'num': 10,  # Можно увеличить до 20-30 для inline, если API позволяет
        'safe': 'off'
    }
    image_data_list = []
    try:
        response = requests.get(search_url, params=params, timeout=20) # Немного увеличим таймаут
        response.raise_for_status()
        results_json = response.json()

        if 'items' in results_json:
            for item in results_json['items']:
                # Убедимся, что есть и основная ссылка, и ссылка на превью
                if 'link' in item and 'image' in item and 'thumbnailLink' in item['image']:
                    image_data_list.append({
                        'id': str(uuid.uuid4()), # Генерируем ID здесь
                        'photo_url': item['link'],
                        'thumbnail_url': item['image']['thumbnailLink'],
                        # Опционально: размеры, если нужны (Telegram сам их определит)
                        # 'photo_width': item['image'].get('width'),
                        # 'photo_height': item['image'].get('height'),
                    })
        logger.info(f"Найдено {len(image_data_list)} изображений с превью для запроса: {query}")
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка: {http_err} - {response.text if 'response' in locals() and response else 'No response text'}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при вызове Google API: {e}")
    except Exception as e:
        logger.error(f"Другая ошибка при поиске изображений: {e}")
    return image_data_list

# --- ОБРАБОТЧИКИ КОМАНД TELEGRAM ---
async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    await update.message.reply_html(
        rf"Привет, {user.mention_html()}! Отправь мне поисковый запрос, или используй меня в inline-режиме: @имя_бота <запрос>",
    )

async def image_search_handler(update: Update, context: CallbackContext) -> None:
    query = update.message.text
    if not query:
        await update.message.reply_text("Пожалуйста, введите поисковый запрос.")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

    images_data = search_images_data(query) # Используем новую функцию

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

# --- ОБРАБОТЧИК INLINE-ЗАПРОСОВ ---
async def inline_query_handler(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    if not query: # Если запрос пустой, ничего не делаем
        return

    logger.info(f"Inline-запрос получен: '{query}'")
    images_data = search_images_data(query) # Используем ту же функцию поиска

    results = []
    for img_data in images_data:
        try:
            results.append(
                InlineQueryResultPhoto(
                    id=img_data['id'], # Используем сгенерированный ранее ID
                    photo_url=img_data['photo_url'],
                    thumbnail_url=img_data['thumbnail_url'],
                    # caption=f"Найдено: {query[:30]}" # Можно добавить подпись к inline-результату
                )
            )
        except Exception as e:
            logger.error(f"Ошибка создания InlineQueryResultPhoto: {e} для ID: {img_data.get('id')}")
            # Пропускаем этот результат, если он некорректен

    # Отвечаем на inline-запрос. Telegram рекомендует кэшировать результаты.
    # cache_time - время в секундах, на которое Telegram может кэшировать результаты для этого запроса.
    # is_personal=True можно установить, если результаты специфичны для пользователя (здесь это не так)
    try:
        await update.inline_query.answer(results[:20], cache_time=300) # Отправляем не более 20 результатов, кэш на 5 минут
        logger.info(f"Отправлено {len(results[:20])} inline-результатов для запроса '{query}'")
    except Exception as e:
        logger.error(f"Ошибка ответа на inline-запрос: {e}")


async def error_handler(update: object, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def main() -> None:
    if not all([TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, GOOGLE_CSE_ID]):
        logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Отсутствуют TELEGRAM_BOT_TOKEN, GOOGLE_API_KEY, или GOOGLE_CSE_ID.")
        return

    application_builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN)
    application = application_builder.build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, image_search_handler))
    application.add_handler(InlineQueryHandler(inline_query_handler)) # <-- ДОБАВЛЕН ОБРАБОТЧИК INLINE
    application.add_error_handler(error_handler)

    run_on_heroku = bool(HEROKU_APP_NAME) and bool(os.environ.get("PORT"))

    if run_on_heroku:
        if not HEROKU_APP_NAME:
            logger.error("HEROKU_APP_NAME не задан, не могу запустить вебхук.")
            return
        webhook_url = f"https://{HEROKU_APP_NAME}.herokuapp.com/{TELEGRAM_BOT_TOKEN}"
        logger.info(f"Запуск бота с вебхуком. Установка вебхука на: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        logger.info("Запуск бота с long polling для локального тестирования...")
        application.run_polling(allowed_updates=Update.ALL_TYPES) # Явно указываем, что принимаем все типы обновлений

if __name__ == '__main__':
    main()