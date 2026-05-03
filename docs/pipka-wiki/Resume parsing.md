#resume #frontend

# Resume parsing

Endpoint `POST /api/profile/resume` принимает PDF / DOCX / TXT, извлекает чистый текст и сохраняет в `UserProfile.resume_text`. Используется как input для построения [[Поиск и индексация#semantic indexing|profile-embedding]] и для [[Скоринг|AI-скоринг-промпта]].

Файл: `app/api/profile.py:upload_resume`. Зависимости: `pdfminer.six` для PDF, stdlib `zipfile`+`ET` для DOCX, никаких внешних либ для TXT.

## Конвейер

```
POST /api/profile/resume (multipart, file=...)
   │
   ├─ size check (≤ MAX_RESUME_UPLOAD_BYTES = 10 MB)
   │     → 413 если больше
   │
   ├─ extension whitelist (.pdf | .docx | .txt)
   │     → 400 на чужие
   │
   ├─ magic-bytes verify
   │     .pdf  → должно начинаться с b"%PDF"
   │     .docx → должно начинаться с b"PK\x03\x04" (zip header)
   │     → 400 если расширение врёт о формате
   │
   ├─ format-specific extract:
   │     .pdf  → pdfminer.high_level.extract_text(BytesIO(content))
   │     .docx → распаковываем zip, читаем word/document.xml,
   │             обходим <w:p>/<w:t>, склеиваем параграфы
   │     .txt  → content.decode("utf-8", errors="ignore")
   │
   ├─ sanitize: strip \x00, .strip()
   │     → 400 если пусто
   │
   ├─ truncate до MAX_RESUME_CHARS = 100 000 символов
   │
   └─ UPDATE user_profiles SET resume_text = :text WHERE id = :pid
         + invalidate_profile_embedding(profile_id)
            ↑ embed_index переиндексирует на следующем тике (см. [[Поиск и индексация]])
```

## Защита от extension-spoofing

Расширение в filename — ненадёжный индикатор. Юзер может загрузить `evil.exe.pdf` или `notes.txt.pdf`. Поэтому первые 4 байта content проверяются против известных magic-bytes:

| Формат | Magic | Что блокирует |
|--------|-------|---------------|
| PDF | `%PDF` | загрузку HTML/exe/script с переименованным расширением |
| DOCX | `PK\x03\x04` | DOCX это zip — все zip начинаются так. Заодно ловит `.docx` который на самом деле doc/odt |
| TXT | (не проверяется) | UTF-8 decode с `errors="ignore"` сам себя защищает |

Не пропустит даже PDF с правильным расширением, если первые байты повреждены — `extract_text` потом всё равно бы упал, но 400 на этапе magic-bytes даёт юзеру осмысленную ошибку.

## DOCX — без python-docx

Намеренно не используем `python-docx` (библиотека ~3MB, тянет lxml). DOCX — это zip с XML, нам нужен только текст параграфов:

```python
zf = zipfile.ZipFile(io.BytesIO(content))
xml_content = zf.read("word/document.xml")
tree = ET.fromstring(xml_content)
for p in tree.iter("{http://...wordprocessingml...}p"):
    texts = [t.text for t in p.iter("{...}t") if t.text]
    if texts:
        paragraphs.append("".join(texts))
text = "\n".join(paragraphs)
```

Минусы: не извлекаем таблицы, изображения, footnotes. Для резюме обычно достаточно. Если в будущем понадобятся таблицы (некоторые европейские резюме приходят в табличном формате) — переход на `python-docx` потребует одну строку.

## PDF — pdfminer.six

Базовый `extract_text` без особых настроек. Хорошо парсит текстовые PDF, плохо — сканы (нужен OCR — см. [[Roadmap]]).

Известные пробелы:
- **Multi-column layouts** — pdfminer склеивает текст по визуальной близости, иногда ломает порядок чтения если резюме в две колонки.
- **Embedded fonts с custom encoding** — некоторые corporate-templates возвращают мусор. Стрипается через `\x00` cleanup, но если 80% текста невалидное — резюме просто не попадёт в embedding.

## Sanitization

После извлечения:

1. `text.replace("\x00", "")` — `\x00` ломает PostgreSQL TEXT-тип (он не любит null-byte).
2. `text.strip()` — leading/trailing whitespace.
3. `if not text: raise 400` — пустой результат значит парсер не справился.
4. `text[:MAX_RESUME_CHARS]` — обрезка до 100k символов. Реалистичный CV ~5-10k. 100k — buffer на CV-портфолио и многоязычные версии в одном файле.

## Связь с embeddings

После UPDATE `resume_text` обязательно вызывается `invalidate_profile_embedding(profile_id)` ([[Поиск и индексация]]):

```sql
UPDATE user_profiles
SET embedding = NULL,
    embedding_model = NULL,
    embedding_updated_at = NULL,
    embedding_profile_hash = NULL
WHERE id = :pid
```

Иначе старый embedding продолжает использоваться при `?semantic=1` в `/api/jobs` и [[Скоринг#этап-15-опционально-semantic-pre-rank|semantic-skip]] — search-результаты не отразят новое резюме.

## Не делает

- **OCR.** Сканированный PDF приходит как картинка — pdfminer вернёт пусто. Поддержка `pdf2image + tesseract` — пункт [[Roadmap]].
- **Auto-detect языка.** Резюме на DE/EN обрабатываются одинаково. Embedding API (`models/gemini-embedding-001`) сам мультиязычный, так что в плане скоринга не критично, но в UI было бы polished показать "Язык резюме: DE/EN".
- **Sections extraction.** `text` хранится как одна сплошная строка. Парсить на "Опыт работы / Образование / Навыки" — для лучших промптов AI-скорера. Сейчас весь текст уходит в Claude/Gemini как контекст профиля.

## Связи

- Triggered by [[Frontend]] (`POST /api/profile/resume` через FormData в `app/static/dashboard.html`).
- Storage: `user_profiles.resume_text` ([[База данных]]).
- Consumers: [[Скоринг]] (через `compute_profile_hash` → инвалидация AI-кэша), [[Поиск и индексация]] (через `embed_index`).
- Validation: [[Безопасность#4-input-validation]] — magic-bytes pattern.

→ [[API]] → [[Frontend]] → [[Безопасность]] → [[Поиск и индексация]] → [[База данных]] → [[Скоринг]] → [[Roadmap]]
