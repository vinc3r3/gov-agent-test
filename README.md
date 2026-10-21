# Goszakup AI-агент

AI-агент для анализа государственных закупок РК по данным OWS v3 (goszakup.gov.kz). Охватывает 27 организаций и 2024–2026 годы.
Агент принимает вопросы на русском и казахском, сам определяет тип вопроса (поиск, сравнение, аналитика, аномалии,
справедливость цены, объёмы) и отвечает проверяемо: с цифрами, размером выборки, методом, уверенностью и ссылками на конкретные договоры.

**LLM ничего не считает.** Все вычисления выполняют SQL и Python. Модель выбирает инструмент и формулирует вывод,
а числа в выводе проходят автоматическую сверку с расчётами.

## Документы для сдачи

| Что требуется в ТЗ | Документ |
|---|---|
| Архитектурное описание (текст и схема) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Схема хранения данных | [docs/DATA_MODEL.md](docs/DATA_MODEL.md) |
| Описание аналитики и метрик (Fair Price и др.) | [docs/METRICS.md](docs/METRICS.md) |
| Примеры ответов AI-агента | [docs/EXAMPLES.md](docs/EXAMPLES.md) |
| Точность (≥ 85%) | [docs/EVAL.md](docs/EVAL.md) |
| Риски и ограничения | [docs/RISKS.md](docs/RISKS.md) |

## Структура

```
backend/
  app/ingest/     OWS v3 клиент, полная и инкрементальная загрузка, планировщик
  app/etl/        сборка core/mart и проверки качества данных
  app/analytics/  Fair Price, аномалии цен, аномалии объёмов, поиск и агрегаты
  app/agent/      LLM-провайдеры (Claude / OpenAI-совместимые), цикл tool use, fact-check, рендер ответа
  app/api/        FastAPI
  sql/            001_raw, 002_cpi (ИПЦ РК), 003_core, 004_mart, 005_roles (read-only роль агента)
  eval/           эталонные вопросы, оценка точности, сборка EXAMPLES.md
  tests/          pytest
frontend/         React + Vite (чат с карточкой ответа по структуре ТЗ)
infra/k8s/        манифесты Kubernetes
docker-compose.yml
```

## Запуск (локально)

```bash
cp .env.example .env            # OWS_TOKEN и ключ LLM (см. раздел «LLM»)
docker compose up -d db

cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.ingest.jobs full        # полная загрузка (около 10 мин)
.venv/bin/python -m app.etl.build                # core + mart + отчёт качества (~10 с)
.venv/bin/uvicorn app.api.main:app --port 8000

cd ../frontend && npm ci && npm run dev          # http://localhost:5173
```

Инкремент вручную: `python -m app.ingest.jobs incremental && python -m app.etl.build`.

## Запуск (Docker / on-prem)

```bash
docker compose up -d --build                     # db, api, scheduler (ежечасный инкремент), web → http://localhost:8080
docker compose --profile local-llm up -d ollama  # опционально: локальная LLM
docker compose exec ollama ollama pull qwen2.5:7b-instruct
# в .env: LLM_PROVIDER=openai_compat
```

Kubernetes: `infra/k8s/goszakup-agent.yaml`. Там Postgres StatefulSet, api и web, CronJob синхронизации и Secret для токенов.

## LLM

Провайдер выбирается в `.env`, код при этом менять не нужно:

| `LLM_PROVIDER` | Где работает модель | Пример настроек |
|---|---|---|
| `anthropic` | Claude API | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-opus-5-5` |
| `openai_compat` | **on-prem**: Ollama / vLLM, данные не покидают контур | `OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1`, `OPENAI_COMPAT_MODEL=qwen2.5:7b-instruct` |
| `openai_compat` | облачный OpenAI-совместимый API: **Gemini**, OpenAI, Groq | Gemini: `OPENAI_COMPAT_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`, `OPENAI_COMPAT_MODEL=gemini-3.8-flash`. OpenAI: `https://api.openai.com/v1`. Groq: `https://api.groq.com/openai/v1` |

Дополнительно: `OPENAI_COMPAT_TEMPERATURE=` (пустое значение — параметр не передаётся, нужно для reasoning-моделей),
`OPENAI_COMPAT_MAX_RETRIES` (повторы при 429/503). Eval в `docs/EVAL.md` прогнан на моделях Gemini.

## Тесты и оценка

```bash
cd backend
.venv/bin/python -m pytest -q          # unit- и интеграционные тесты (сверка с независимым SQL)
.venv/bin/python -m eval.run_eval      # точность агента → docs/EVAL.md
.venv/bin/python -m eval.make_examples # примеры ответов из прогона → docs/EXAMPLES.md
```

Полезные флаги `run_eval`: `--resume` (не перезапрашивать уже отвеченные вопросы, экономит квоту LLM),
`--pause 10` (пауза между вопросами для бесплатных тарифов), `--only id1,id2` (только выбранные вопросы),
`--from-log` (пересчитать оценку по сохранённым ответам без вызова LLM).

## API

`POST /api/ask {"question": "..."}` возвращает `{question_type, language, sections{verdict, data, analytics, metric, confidence, top_k}, markdown, evidence, trace}`.
Остальные эндпоинты: `GET /api/health`, `GET /api/dq` (отчёт качества данных), `GET /api/examples`, `GET /api/history` (последние вопросы), `GET /api/answer/{id}` (сохранённый ответ целиком).
