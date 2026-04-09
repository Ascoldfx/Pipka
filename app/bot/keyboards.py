from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# --- Main menu ---

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск вакансий", callback_data="menu_search")],
        [InlineKeyboardButton("📋 Мои вакансии", callback_data="menu_my_jobs")],
        [InlineKeyboardButton("⚙️ Профиль / Фильтры", callback_data="menu_profile")],
        [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")],
    ])

# --- Search options ---

def search_type_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Регион (Саксония + Галле)", callback_data="search_regional")],
        [InlineKeyboardButton("🇩🇪 Вся Германия", callback_data="search_germany")],
        [InlineKeyboardButton("🌐 International / English (DE)", callback_data="search_international")],
        [InlineKeyboardButton("🌍 Европа (DACH + NL)", callback_data="search_europe")],
        [InlineKeyboardButton("🎯 Свой запрос", callback_data="search_custom")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])

# --- "Show more" after search results ---

def show_more_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Показать ещё 15", callback_data="show_more")],
    ])

# --- Job actions ---

def job_actions(job_db_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 AI Анализ", callback_data=f"ai_{job_db_id}"),
            InlineKeyboardButton("💾 Сохранить", callback_data=f"save_{job_db_id}"),
        ],
        [
            InlineKeyboardButton("📨 Отправил резюме", callback_data=f"applied_{job_db_id}"),
        ],
    ])

# --- Application status ---

def status_keyboard(app_id: int) -> InlineKeyboardMarkup:
    statuses = [
        ("📝 Applied", "applied"),
        ("🗣 Interview", "interviewing"),
        ("🎉 Offer", "offer"),
        ("❌ Rejected", "rejected"),
        ("🚫 Withdrawn", "withdrawn"),
    ]
    buttons = [[InlineKeyboardButton(label, callback_data=f"status_{app_id}_{code}")] for label, code in statuses]
    return InlineKeyboardMarkup(buttons)

# --- Profile setup ---

def profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Резюме (текст)", callback_data="prof_resume")],
        [InlineKeyboardButton("🎯 Целевые должности", callback_data="prof_titles")],
        [InlineKeyboardButton("💰 Мин. зарплата", callback_data="prof_salary")],
        [InlineKeyboardButton("🌐 Языки", callback_data="prof_languages")],
        [InlineKeyboardButton("📍 Локация / Режим", callback_data="prof_location")],
        [InlineKeyboardButton("🏭 Индустрии", callback_data="prof_industries")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])

# --- Pagination ---

def pagination(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}_page_{page - 1}"))
    buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}_page_{page + 1}"))
    return InlineKeyboardMarkup([buttons])
