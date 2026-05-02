#frontend #ui

# Frontend (SPA)

Один HTML + один JS-файл — лёгкий vanilla SPA без сборщиков. Сервится FastAPI'ем как статика из `app/static/`.

| Файл | Размер | Назначение |
|------|--------|-----------|
| `app/static/dashboard.html` | ~2000 строк | Каркас + inline CSS + i18n-словари + ~70% логики |
| `app/static/js/app.js` | ~600 строк | Инициализация, fetch-wrapper с CSRF, обработчики табов |
| `app/static/infographic.html` | отдельный | Публичная статистика для лэндинга (`/infographic`) |
| `app/static/llms.txt` | манифест | Описание проекта для AI-краулеров |

## Структура

```
DOMContentLoaded
   ↓
initApp()  ──→ GET /api/me → определяет authenticated/role/csrf_token
              ├─ if guest: показать "Sign in", скрыть auth-only вкладки, min_score=0
              └─ if user/admin: загрузить профиль, показать табы Inbox/Applied/Settings
   ↓
   запускает loadJobs() / loadStats() / loadCountries() параллельно
   ↓
switchTab(name) — переключение вкладок (jobs / inbox / applied / rejected / settings / ops)
   ↓
обработчики событий на фильтры (search / score / country / source / include_closed)
```

## fetch wrapper + CSRF

Файл: `app/static/js/app.js`. Глобальный `window.fetch` обёрнут на старте — на любых **POST/PUT/PATCH/DELETE** автоматически подмешивается заголовок `X-CSRF-Token` из cookie:

```js
function _readCsrfCookie() {
  const m = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}
const _UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const _origFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const method = (init.method || 'GET').toUpperCase();
  if (_UNSAFE.has(method)) {
    const token = _readCsrfCookie();
    if (token) {
      init.headers = new Headers(init.headers || {});
      if (!init.headers.has('X-CSRF-Token')) init.headers.set('X-CSRF-Token', token);
    }
  }
  return _origFetch(input, init);
};
```

Почему cookie, а не из `/api/me` JSON: cookie задаётся на каждом GET-ответе сервером ([[Безопасность#3-csrf-double-submit]]) и доступен JS (`HttpOnly=False`). На GET вообще ничего лишнего не делаем.

Все остальные `fetch(...)` в коде (loadJobs, doAction, saveProfile…) пишутся как обычно — обёртка прозрачна.

## i18n — 4 языка

`TRANSLATIONS` в `dashboard.html:410` — четыре словаря (EN / RU / DE / ES). Активный язык в localStorage (`pipka_lang`), по умолчанию RU.

```js
const TRANSLATIONS = {
  en: { 'nav.jobs': 'All Jobs', 'nav.inbox': 'Inbox', ... },
  ru: { 'nav.jobs': 'Все вакансии', 'nav.inbox': 'Входящие', ... },
  de: { 'nav.jobs': 'Alle Stellen', ... },
  es: { 'nav.jobs': 'Todos los empleos', ... },
};

function T(key) {
  return (TRANSLATIONS[_lang] || TRANSLATIONS.en)[key] || TRANSLATIONS.en[key] || key;
}

function setLang(lang) {
  _lang = lang;
  localStorage.setItem('pipka_lang', lang);
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const attr = el.getAttribute('data-i18n-attr');  // optional: переводим атрибут (placeholder/title), не textContent
    if (attr) el.setAttribute(attr, T(key));
    else el.textContent = T(key);
  });
}
```

Применение: на каждом UI-элементе ставится `data-i18n="some.key"`. Опционально `data-i18n-attr="placeholder"` если переводим аттрибут вместо текста.

**Исключение из i18n:** Ops-таб (`tc-ops`) — хардкод на русском, не переводится. См. [[Ops панель]].

При добавлении нового UI-текста — нужно вписывать в **все 4 словаря**. Я в течение последних релизов несколько раз пропускал DE/ES для новых полей (`filter.includeClosed`, `settings.watchlist`) — стоит делать привычкой проверять.

## Dark mode

Кнопка 🌙/☀️ в header'е. Переключает класс `dark` на `<html>`, сохраняется в `localStorage["pipka_theme"]`. Автодетект системной темы через `prefers-color-scheme` при первом заходе.

CSS-переменные в `:root` и `:root.dark` — все цвета через `var(--bg)`, `var(--text)`, `var(--accent)` и т.д.

## Multi-select country dropdown

Кастомный (не `<select multiple>`, потому что HTML-стандартный уродливый). Реализация в `dashboard.html`:

- Кнопка с label `All Countries` / `3 countries` / `Germany`.
- Dropdown с чекбоксами стран, грузится через `GET /api/countries` с count'ом.
- Selected — в `_selCountries: Set<string>`.
- При изменении → `loadJobs()` с `?countries=de,at,nl`.

Кнопки `All` / `None` для bulk-toggle.

## Состояние SPA

```js
const S = {
  page: 1,
  perPage: 50,
  sort: 'score',
  order: 'desc',
  totalPages: 1,
  tabStatus: '',           // 'new' / 'applied' / 'rejected' / ''
  role: 'guest',
  authenticated: false,
  activeTab: 'jobs',
  opsWindow: 24
};
```

Активная вкладка persists в localStorage (`pipka_tab`) для авторизованных. Для guest — всегда `jobs`.

## Render hot path

`loadJobs()`:
1. Собирает URLSearchParams из всех фильтров (page / perPage / sort / order / min_score / search / source / countries / status / tabStatus / include_closed).
2. `fetch('/api/jobs?' + p)` → JSON.
3. Для каждой строки → `jobRow(j)` → HTML-строка.
4. `tbody.innerHTML = rows.join('')`. Мы НЕ используем virtual DOM / React / Vue — просто innerHTML. Для 50 строк/страница работает мгновенно.

Карточка:
- `job-title` с line-through если `url_status='closed'` и `include_closed=1` (см. [[Проверка ссылок]]).
- Source-tag с золотым watchlist-бэйджем если `source='watchlist'` ([[Watchlist]]).
- `+1` бэйдж если `merged_sources.length > 1` ([[Дедупликация]]).
- Score-pill: green/yellow/red по диапазону.
- Action-кнопки applied / reject / 🤖 AI Анализ — только для авторизованных.

## Безопасные операции в HTML

`esc(s)` — html-escape строки перед вставкой через innerHTML.
`safeUrl(u)` — валидирует URL (только http://, https://, mailto:), отбрасывает `javascript:` и data:.

## Что не делает frontend

- **Не валидирует profile-формы перед отправкой.** Сервер ([[API#profile]]) — единственный источник правды.
- **Не использует localStorage для критичных данных.** Только UI-настройки (язык, тема, активная вкладка).
- **Не кеширует API-ответы локально.** Все `fetch()` идут с `cache: 'no-store'` — сервер всегда отдаёт свежий JSON (см. [[Observability#nocacheapimiddleware]]).

## TODO / [[Roadmap]]

- **Реактивность.** При смене source-filter'а сейчас полная перезагрузка таблицы — на 200-row'ах ОК, на 1000+ начнёт жмить. Можно ввести Alpine.js или Svelte light.
- **Pagination keyset вместо offset.** При `page=50` postgres сделает `OFFSET 2500` — медленно. Лучше `WHERE id < last_id` cursor.
- **Skeleton loaders** вместо `Loading...` — UX чуть приятнее.
- **Semantic search в UI.** Сейчас `?semantic=1` доступен только через прямой URL. Стоит добавить toggle "Точный / Семантический поиск" (см. [[Поиск и индексация]]).
- **Доперевести `settings.watchlist`/`settings.companiesLabel` etc.** на все 4 языка консистентно.

→ [[API]] → [[Auth]] → [[Безопасность]] → [[Ops панель]] → [[Поиск и индексация]] → [[Проверка ссылок]] → [[Watchlist]] → [[Roadmap]]
