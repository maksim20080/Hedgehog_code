import os
import sqlite3
import asyncio
import logging
import requests
from datetime import datetime
from typing import Dict, Optional, Tuple, List

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.error import Forbidden
from huggingface_hub import InferenceClient

# ======================= КОНФИГУРАЦИЯ =======================
TELEGRAM_TOKEN = "8711792105:AAFu1yBMbCp2eyv4a5kJU58Ug6r5w7XtHnU"
HF_API_KEY = "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"

client = InferenceClient(api_key=HF_API_KEY)
MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

AGE, INTERESTS, EDUCATION, SKILLS = range(4)

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            age INTEGER,
            interests TEXT,
            education TEXT,
            skills TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            recommendation_type TEXT,
            content TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            language TEXT DEFAULT 'ru'
        )
    ''')
    conn.commit()
    conn.close()

def save_user(user_id, username, age, interests, education, skills):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users (user_id, username, age, interests, education, skills, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, age, interests, education, skills, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('SELECT age, interests, education, skills FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"age": row[0], "interests": row[1], "education": row[2], "skills": row[3]}
    return None

def save_recommendation(user_id, rec_type, content):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO recommendations (user_id, recommendation_type, content, created_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, rec_type, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_last_recommendation(user_id):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT content FROM recommendations 
        WHERE user_id = ? AND recommendation_type = "career_plan" 
        ORDER BY created_at DESC LIMIT 1
    ''', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_user_language(user_id):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('SELECT language FROM user_settings WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    set_user_language(user_id, 'ru')
    return 'ru'

def set_user_language(user_id, language):
    conn = sqlite3.connect('career_bot.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO user_settings (user_id, language) VALUES (?, ?)', (user_id, language))
    conn.commit()
    conn.close()

# ======================= ТЕКСТЫ =======================
def get_text(lang, key):
    texts = {
        'ru': {
            'welcome': "👋 Привет! Я AI-карьерный навигатор для молодёжи 16-22 лет.\n\nПомогу тебе определиться с профессией, составить план развития и найти стажировки.\n\nНажми кнопку «Пройти диагностику», чтобы начать! 🚀",
            'diagnostic_start': "📝 Начнём диагностику! Сколько тебе лет? (от 16 до 22)",
            'age_invalid': "Пожалуйста, введи возраст от 14 до 30 лет.",
            'age_number': "Введи число, например: 19",
            'ask_interests': "🎨 Какие у тебя интересы? (например: программирование, дизайн, маркетинг)",
            'ask_education': "🎓 Какое у тебя образование? (например: школьник, студент колледжа/вуза)",
            'ask_skills': "💪 Перечисли свои навыки через запятую. Пример: Python, Figma, английский B2",
            'processing': "⏳ Обрабатываю данные через AI... Это займёт 5-10 секунд.",
            'diagnostic_done': "✅ Диагностика завершена!\n\n{}",
            'no_data': "Сначала пройди диагностику через кнопку «Пройти диагностику»",
            'no_recommendations': "⏳ Генерирую свежие рекомендации...",
            'searching_jobs': "🔎 Ищу стажировки и вакансии для новичков...",
            'no_vacancies': "😕 Не удалось найти подходящие вакансии.\nПопробуй:\n• Добавить больше навыков в диагностике\n• Искать вручную на hh.ru с фильтром «без опыта»\n\n🔗 Ссылка для ручного поиска: {}",
            'help_text': "🤖 *Как работает бот:*\n\n1. *Диагностика* — заполни анкету, AI проанализирует твои данные\n2. *Рекомендации* — получишь список подходящих профессий и план развития\n3. *Вакансии* — поиск стажировок на hh.ru под твои навыки\n\nВсе данные сохраняются, ты можешь вернуться к рекомендациям в любой момент.\n\nКоманды:\n/start — главное меню\n/cancel — отменить диагностику",
            'language_changed': "🌐 Язык изменён на русский.",
            'use_buttons': "Используй кнопки меню для навигации 👇",
            'canceled': "Диагностика отменена. Нажми «Пройти диагностику», когда будешь готов!",
            'trends_label': "🎯 *Твои профессиональные склонности:*",
            'plan_label': "📚 *План развития:*",
            'jobs_header': "💼 *Нашёл подходящие предложения:*",
            'salary_unknown': "💰 з/п не указана"
        },
        'en': {
            'welcome': "👋 Hi! I'm an AI career navigator for youth aged 16-22.\n\nI'll help you choose a profession, create a development plan, and find internships.\n\nPress 'Start diagnosis' to begin! 🚀",
            'diagnostic_start': "📝 Let's start the diagnosis! How old are you? (16 to 22)",
            'age_invalid': "Please enter an age between 14 and 30.",
            'age_number': "Enter a number, e.g., 19",
            'ask_interests': "🎨 What are your interests? (e.g., programming, design, marketing)",
            'ask_education': "🎓 What is your education? (e.g., high school, college student, university student)",
            'ask_skills': "💪 List your skills separated by commas. Example: Python, Figma, English B2",
            'processing': "⏳ Processing your data with AI... This will take 5-10 seconds.",
            'diagnostic_done': "✅ Diagnosis complete!\n\n{}",
            'no_data': "Please complete the diagnosis first using the 'Start diagnosis' button.",
            'no_recommendations': "⏳ Generating fresh recommendations...",
            'searching_jobs': "🔎 Looking for internships and entry-level jobs...",
            'no_vacancies': "😕 Could not find suitable vacancies.\nTry:\n• Add more skills in diagnosis\n• Search manually on hh.ru with 'no experience' filter\n\n🔗 Manual search link: {}",
            'help_text': "🤖 *How the bot works:*\n\n1. *Diagnosis* — fill out the form, AI will analyze your data\n2. *Recommendations* — get a list of suitable professions and a development plan\n3. *Jobs* — search for internships on hh.ru based on your skills\n\nAll data is saved, you can return to recommendations anytime.\n\nCommands:\n/start — main menu\n/cancel — cancel diagnosis",
            'language_changed': "🌐 Language changed to English.",
            'use_buttons': "Use the menu buttons for navigation 👇",
            'canceled': "Diagnosis cancelled. Press 'Start diagnosis' when you're ready!",
            'trends_label': "🎯 *Your professional tendencies:*",
            'plan_label': "📚 *Development plan:*",
            'jobs_header': "💼 *Found suitable offers:*",
            'salary_unknown': "💰 salary not specified"
        }
    }
    return texts.get(lang, texts['ru']).get(key, key)

# ======================= AI =======================
async def query_ai(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    try:
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.7,
                top_p=0.95
            )
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI error: {e}")
        return ""

async def analyze_career_async(age, interests, education, skills, language):
    if language == 'ru':
        prompt = f"""Ты — карьерный консультант для молодёжи 16-22 лет. Пользователю {age} лет.
Образование: {education}
Интересы: {interests}
Навыки: {skills}

Ответь строго на русском языке в формате:
СКЛОННОСТИ: (2-3 подходящие профессии или сферы)
ПЛАН: (конкретные шаги на ближайший год: курсы, проекты, стажировки)"""
    else:
        prompt = f"""You are a career advisor for youth aged 16-22. User is {age} years old.
Education: {education}
Interests: {interests}
Skills: {skills}

Answer strictly in English in the format:
TENDENCIES: (2-3 suitable professions or fields)
PLAN: (specific steps for the next year: courses, projects, internships)"""
    
    response = await query_ai(prompt)
    
    if language == 'ru':
        tendencies_label = "СКЛОННОСТИ:"
        plan_label = "ПЛАН:"
        fallback_tendencies = "IT, дизайн, маркетинг, аналитика данных"
        fallback_plan = "Пройди бесплатные курсы: 'Введение в Python', 'Figma для начинающих', 'Основы SMM'."
    else:
        tendencies_label = "TENDENCIES:"
        plan_label = "PLAN:"
        fallback_tendencies = "IT, design, marketing, data analytics"
        fallback_plan = "Take free courses: 'Introduction to Python', 'Figma for Beginners', 'SMM Fundamentals'."
    
    tendencies = ""
    plan = ""
    if tendencies_label in response and plan_label in response:
        parts = response.split(plan_label)
        tendencies = parts[0].replace(tendencies_label, "").strip()
        plan = parts[1].strip()
    else:
        tendencies = response[:300]
        plan = fallback_plan
    
    if len(tendencies) < 5:
        tendencies = fallback_tendencies
        plan = fallback_plan
    
    return tendencies, plan

# ======================= ПОИСК ВАКАНСИЙ (ТОЛЬКО HH.RU - БЫСТРО) =======================
def search_hh(skills: str, interests: str) -> List[Dict]:
    """Умный и устойчивый поиск вакансий"""

    query = f"{interests} {skills}".replace(",", " ")

    url = "https://api.hh.ru/vacancies"
    params = {
        "text": query,
        "per_page": 20,
        "experience": "noExperience",
        "order_by": "publication_time"
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        all_vacancies = []
        filtered = []

        # ключевые слова по интересам
        keywords = interests.lower().split()

        for item in data.get("items", []):
            title = item.get("name", "")
            title_lower = title.lower()

            salary = item.get("salary")
            salary_text = None

            if salary:
                if salary.get("from") and salary.get("to"):
                    salary_text = f"{salary['from']}–{salary['to']} {salary.get('currency', '')}"
                elif salary.get("from"):
                    salary_text = f"от {salary['from']} {salary.get('currency', '')}"
                elif salary.get("to"):
                    salary_text = f"до {salary['to']} {salary.get('currency', '')}"

            job = {
                "title": title,
                "company": item.get("employer", {}).get("name", "Не указана"),
                "link": item.get("alternate_url", "#"),
                "salary": salary_text,
                "source": "hh.ru"
            }

            all_vacancies.append(job)

            # мягкая фильтрация (НЕ жесткая)
            if any(word in title_lower for word in keywords):
                filtered.append(job)

        # если нашли релевантные — показываем их
        if filtered:
            return filtered[:5]

        # если нет — показываем просто первые вакансии
        return all_vacancies[:5]

    except Exception as e:
        logger.error(f"HH search error: {e}")
        return []

# ======================= TELEGRAM БОТ =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    keyboard = [
        [KeyboardButton("🎯 Пройти диагностику" if lang == 'ru' else "🎯 Start diagnosis")],
        [KeyboardButton("📋 Мои рекомендации" if lang == 'ru' else "📋 My recommendations")],
        [KeyboardButton("💼 Найти вакансии" if lang == 'ru' else "💼 Find jobs")],
        [KeyboardButton("🌐 Сменить язык" if lang == 'ru' else "🌐 Change language")],
        [KeyboardButton("❓ Помощь" if lang == 'ru' else "❓ Help")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(get_text(lang, 'welcome'), reply_markup=reply_markup)

async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current = get_user_language(user_id)
    new_lang = 'en' if current == 'ru' else 'ru'
    set_user_language(user_id, new_lang)
    await start(update, context)

async def diagnostic_start(update, context):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(get_text(lang, 'diagnostic_start'))
    return AGE

async def diagnostic_age(update, context):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    try:
        age = int(update.message.text)
        if age < 14 or age > 30:
            await update.message.reply_text(get_text(lang, 'age_invalid'))
            return AGE
        context.user_data['age'] = age
        await update.message.reply_text(get_text(lang, 'ask_interests'))
        return INTERESTS
    except ValueError:
        await update.message.reply_text(get_text(lang, 'age_number'))
        return AGE

async def diagnostic_interests(update, context):
    context.user_data['interests'] = update.message.text
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(get_text(lang, 'ask_education'))
    return EDUCATION

async def diagnostic_education(update, context):
    context.user_data['education'] = update.message.text
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(get_text(lang, 'ask_skills'))
    return SKILLS

async def diagnostic_skills(update, context):
    context.user_data['skills'] = update.message.text
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    lang = get_user_language(user_id)

    save_user(
        user_id, username,
        context.user_data['age'],
        context.user_data['interests'],
        context.user_data['education'],
        context.user_data['skills']
    )

    await update.message.reply_text(get_text(lang, 'processing'))
    await update.message.chat.send_action(action="typing")

    try:
        tendencies, plan = await analyze_career_async(
            context.user_data['age'],
            context.user_data['interests'],
            context.user_data['education'],
            context.user_data['skills'],
            lang
        )
    except Exception as e:
        logger.error(f"AI error: {e}")
        if lang == 'ru':
            tendencies = "IT-сфера, цифровой маркетинг, дизайн"
            plan = "Пройдите бесплатные курсы на Stepik или Coursera."
        else:
            tendencies = "IT, digital marketing, design"
            plan = "Take free courses on Stepik or Coursera."

    full_recommendation = f"{get_text(lang, 'trends_label')}\n{tendencies}\n\n{get_text(lang, 'plan_label')}\n{plan}"
    save_recommendation(user_id, "career_plan", full_recommendation)

    await update.message.reply_text(
        get_text(lang, 'diagnostic_done').format(full_recommendation) +
        "\n\n" + ("Теперь ты можешь:\n• Нажать «Найти вакансии» для поиска стажировок\n• Нажать «Мои рекомендации», чтобы повторить план"
                  if lang == 'ru' else "\n\nNow you can:\n• Press 'Find jobs' to search for internships\n• Press 'My recommendations' to review the plan"),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_diagnostic(update, context):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(get_text(lang, 'canceled'))
    return ConversationHandler.END

async def show_recommendations(update, context):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    user_data = get_user(user_id)
    if not user_data:
        await update.message.reply_text(get_text(lang, 'no_data'))
        return

    last_rec = get_last_recommendation(user_id)
    if last_rec:
        await update.message.reply_text(f"📌 *{'Твои рекомендации' if lang == 'ru' else 'Your recommendations'}:*\n\n{last_rec}", parse_mode="Markdown")
    else:
        await update.message.reply_text(get_text(lang, 'no_recommendations'))
        await update.message.chat.send_action(action="typing")
        tendencies, plan = await analyze_career_async(
            user_data['age'], user_data['interests'],
            user_data['education'], user_data['skills'], lang
        )
        full_rec = f"{get_text(lang, 'trends_label')}\n{tendencies}\n\n{get_text(lang, 'plan_label')}\n{plan}"
        save_recommendation(user_id, "career_plan", full_rec)
        await update.message.reply_text(f"📌 *{'Твои рекомендации' if lang == 'ru' else 'Your recommendations'}:*\n\n{full_rec}", parse_mode="Markdown")

async def search_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    user_data = get_user(user_id)
    if not user_data:
        await update.message.reply_text(get_text(lang, 'no_data'))
        return

    await update.message.reply_text(get_text(lang, 'searching_jobs'))
    
    # Асинхронно выполняем поиск (не блокируем бота)
    loop = asyncio.get_event_loop()
    vacancies = await loop.run_in_executor(
        None,
        lambda: search_hh(user_data['skills'], user_data['interests'])
    )
    
    if not vacancies:
        # Формируем ссылку для ручного поиска
        query_part = f"{user_data['skills']} {user_data['interests']}".replace(" ", "+")
        hh_link = f"https://hh.ru/search/vacancy?text={query_part}&experience=noExperience"
        await update.message.reply_text(
            get_text(lang, 'no_vacancies').format(hh_link),
            disable_web_page_preview=True
        )
        return

    message = get_text(lang, 'jobs_header') + "\n\n"
    for i, job in enumerate(vacancies, 1):
        salary_text = f"💰 {job['salary']}" if job['salary'] else f"💰 {get_text(lang, 'salary_unknown')}"
        message += f"{i}. *{job['title']}*\n   🏢 {job['company']}\n   {salary_text}\n   🔗 {job['link']}\n   📍 {job['source']}\n\n"
    await update.message.reply_text(message, parse_mode="Markdown", disable_web_page_preview=True)

async def handle_menu(update, context):
    text = update.message.text
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    
    if "Пройти диагностику" in text or "Start diagnosis" in text:
        await diagnostic_start(update, context)
    elif "Мои рекомендации" in text or "My recommendations" in text:
        await show_recommendations(update, context)
    elif "Найти вакансии" in text or "Find jobs" in text:
        await search_jobs(update, context)
    elif "Сменить язык" in text or "Change language" in text:
        await change_language(update, context)
    elif "Помощь" in text or "Help" in text:
        await help_command(update, context)
    else:
        await update.message.reply_text(get_text(lang, 'use_buttons'))

async def help_command(update, context):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(get_text(lang, 'help_text'), parse_mode="Markdown")

async def error_handler(update, context):
    if isinstance(context.error, Forbidden):
        logger.warning(f"User {update.effective_user.id} blocked the bot")
        return
    logger.error(f"Unhandled error: {context.error}")

# ======================= ЗАПУСК =======================
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("diagnostic", diagnostic_start),
            MessageHandler(filters.Regex("Пройти диагностику|Start diagnosis"), diagnostic_start)
        ],
        states={
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, diagnostic_age)],
            INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, diagnostic_interests)],
            EDUCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, diagnostic_education)],
            SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, diagnostic_skills)],
        },
        fallbacks=[CommandHandler("cancel", cancel_diagnostic)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("language", change_language))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_error_handler(error_handler)

    logger.info("🚀 Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
