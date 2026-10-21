# Goszakup AI-агент

AI-агент для анализа госзакупок РК по данным OWS v3: 27 организаций, 2024–2026.
Понимает вопросы на русском и казахском, сам определяет тип вопроса (поиск, сравнение, аналитика, аномалии,
справедливость цены, объёмы) и отвечает с цифрами, размером выборки, методом, уверенностью и ссылками на договоры.

**LLM ничего не считает:** расчёты выполняют SQL и Python, а числа в выводе модели сверяются с результатами расчётов.

## Документы

| ТЗ | Документ |
|---|---|
| Архитектура (текст и схема) | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Схема хранения | [DATA_MODEL.md](docs/DATA_MODEL.md) |
| Аналитика и метрики, Fair Price | [METRICS.md](docs/METRICS.md) |
| Примеры ответов | [EXAMPLES.md](docs/EXAMPLES.md) |
| Точность (≥ 85%) | [EVAL.md](docs/EVAL.md) |
| Риски и ограничения | [RISKS.md](docs/RISKS.md) |

## Структура

```
backend/app/    ingest · etl · analytics · agent · api
backend/sql/    raw → cpi → core → mart → roles
backend/eval/   эталонные вопросы, оценка, сборка EXAMPLES.md
frontend/       React + Vite
infra/k8s/      манифесты Kubernetes
```

## Запуск

```bash
cp .env.example .env                     # OWS_TOKEN и ключ LLM
docker compose up -d db
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.ingest.jobs full # полная загрузка, ~10 мин
.venv/bin/python -m app.etl.build        # витрины и проверки качества, ~10 с
.venv/bin/uvicorn app.api.main:app --port 8000
cd ../frontend && npm ci && npm run dev  # http://localhost:5173
```

Всё в Docker: `docker compose up -d --build` → http://localhost:8080 (вместе с планировщиком ежечасного обновления).
Kubernetes: `infra/k8s/goszakup-agent.yaml`.

## LLM

| `LLM_PROVIDER` | Модель |
|---|---|
| `anthropic` | Claude API (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`) |
| `openai_compat` | любой OpenAI-совместимый API: on-prem Ollama/vLLM или облачные Gemini, OpenAI, Groq (`OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_MODEL`) |

Примеры настроек — в `.env.example`.

## Тесты и оценка

```bash
cd backend
.venv/bin/python -m pytest -q             # тесты, в т.ч. сверка с независимым SQL
.venv/bin/python -m eval.run_eval         # точность → docs/EVAL.md (флаги: --resume, --pause, --only, --from-log)
.venv/bin/python -m eval.make_examples    # примеры → docs/EXAMPLES.md
```

## API

`POST /api/ask {"question": "..."}` — ответ агента. Также `GET /api/health`, `/api/dq`, `/api/examples`, `/api/history`, `/api/answer/{id}`.
