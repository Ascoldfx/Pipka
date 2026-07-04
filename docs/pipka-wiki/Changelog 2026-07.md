#changelog

# Changelog июль 2026

## 4 июля 2026

### «Отклонённые вакансии возвращаются» — скрыты из дефолтного списка

Пользователь отклоняет вакансию (✖) в «Все вакансии» → `loadJobs()` перерисовывает список → строка остаётся на месте (лишь подсвечен ✖). Ощущалось как «вакансия вернулась». Причина: фильтр `status` применялся только на вкладках Inbox/Applied/Rejected, дефолтный список показывал все 667 rejected вперемешку с активными. Дубликаты-«возвращенцы» через пересбор с новым job_id исключены проверкой (0 совпадений по dedup_hash).

- `app/api/jobs.py` — `GET /api/jobs` без `status` теперь добавляет `Application.status IS NULL OR != 'rejected'`. Отклонённые остаются доступны на вкладке Rejected.

### NVIDIA: reraise=True терял nvidia_exhausted ивенты

В логах копились `NVIDIA call failed (batch=15):` с пустым сообщением — это `httpx.ReadTimeout` (пустой `str()`). Из-за `reraise=True` tenacity пробрасывал исходное исключение вместо `RetryError`, ветка `except RetryError` (пишущая `nvidia_exhausted` в Ops) никогда не срабатывала. Тот же класс бага, что чинили в Gemini-брейкере 27.05.2026.

- `app/scoring/nvidia_matcher.py` — `reraise=False` в `AsyncRetrying`; в generic-except добавлено имя типа исключения (httpx-таймауты строкифицируются в пустоту).

См. [[Скоринг]], [[API]], [[Ops панель]].

---

→ [[Changelog 2026-06]] → [[Changelog 2026-05]] → [[Roadmap]] → [[API]] → [[Скоринг]] → [[Трекер]]
