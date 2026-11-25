# API Gateway / Aggregator — MVP

Единый асинхронный REST-шлюз: **Python 3.12 + FastAPI + httpx + motor/MongoDB + Docker Compose**.
Ядро оркестрирует плагины-коннекторы, детерминированно кэширует ответы, умеет отдавать протухший кэш при падении внешнего источника и возвращает единый формат ответа.

---

## 1. Дерево файлов проекта

```text
api-gateway/
├── .dockerignore
├── .env.example                 # Все настройки: порты, MongoDB, TTL, таймауты
├── .gitignore
├── Dockerfile                   # Multi-stage python:3.12-slim, non-root, uvicorn
├── README.md
├── docker-compose.yml           # api + mongo:7.0, healthcheck, volume, общая сеть
├── pyproject.toml               # pytest / ruff config
├── requirements.txt             # Зафиксированные runtime-зависимости
├── requirements-dev.txt         # Тесты и линтер
├── app/
│   ├── __init__.py
│   ├── main.py                  # create_app(), lifespan, подключение роутеров
│   ├── config.py                # pydantic-settings, валидация env
│   ├── container.py             # DI-контейнер: HTTP-клиент, БД, реестр, сервисы
│   ├── database.py              # motor-клиент, ping/retry, TTL-индексы при старте
│   ├── errors.py                # Доменные исключения
│   ├── logging_config.py        # Структурированный root-логгер
│   ├── schemas.py               # Единый QueryEnvelope, QueryRequest, ErrorPayload
│   ├── utils.py                 # UTC/ISO, канонический JSON, helper'ы валидации
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py              # FastAPI Depends → AppContainer
│   │   ├── exception_handlers.py# Любая ошибка → стандартная обертка
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py        # /api/v1
│   │       ├── query.py         # GET/POST /query/{module_name}
│   │       ├── cache.py         # DELETE /cache/{module_name}
│   │       └── system.py        # /health, /modules
│   ├── services/
│   │   ├── __init__.py
│   │   ├── cache_service.py     # SHA-256 ключ, get/set/delete, TTL-документ
│   │   └── query_service.py     # Оркестрация: live/cache/force_refresh/fallback
│   └── connectors/
│       ├── __init__.py          # Публичный API плагинной подсистемы
│       ├── base.py              # ABC BaseConnector + params_model + TTL
│       ├── registry.py          # @register_connector + auto-discovery
│       ├── exchange_rates.py    # Реальный публичный API open.er-api.com
│       └── slow_repository.py   # Имитация внешней SQL/NoSQL витрины, sleep(2)
└── tests/
    ├── __init__.py
    ├── conftest.py              # TestClient + mongomock_motor + MockTransport
    ├── test_api.py              # live → cache → stale_cache, ошибки, инвалидация
    └── test_cache_keys.py       # Детерминированность ключа
```

Создаваемый пользователем `.env` в репозиторий не попадает (см. `.gitignore`).

---

## 2. Архитектура

```text
HTTP client
   │  GET/POST /api/v1/query/{module_name}?force_refresh=true
   ▼
FastAPI router (не знает о конкретных источниках)
   ▼
QueryService  ──── CacheService ──── MongoDB (motor, TTL index)
   │                    ▲
   │                    └── SHA-256(module + canonical_json(normalized_params))
   ▼
ConnectorRegistry (auto-discovery app/connectors/*.py)
   ▼
BaseConnector.fetch()  ──── httpx.AsyncClient ──── внешний REST/SQL/парсер
```

### Плагин / Адаптер

Новый источник добавляется **одним файлом** в `app/connectors/` и декоратором внутри этого файла. `registry.discover_connectors()` при старте импортирует все модули пакета, поэтому `main.py`, `query.py`, `cache_service.py` и `query_service.py` менять не нужно:

```python
# app/connectors/weather.py
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ConnectorUpstreamError


class WeatherParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=80)


@register_connector
class WeatherConnector(BaseConnector):
    name = "weather"
    description = "Weather from an external provider"
    params_model = WeatherParams

    @property
    def cache_ttl_seconds(self) -> int:
        return 600  # индивидуальный TTL модуля

    async def fetch(self, params: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.prepare_params(params)
        try:
            response = await self.http_client.get(
                f"https://example.test/weather?city={normalized['city']}",
                timeout=self.http_timeout,
            )
            response.raise_for_status()
            return {"city": normalized["city"], "raw": response.json()}
        except Exception as exc:  # в проде сузить до httpx-исключений
            raise ConnectorUpstreamError(self.name, f"Weather API failed: {exc}") from exc
```

Имя класса-коннектора должно быть уникальным `snake_case`; `params_model` нормализует параметры **до** вычисления ключа кэша.

### Умное кэширование

| Аспект | Решение |
| --- | --- |
| Ключ | `SHA256(f"{module}:{canonical_json(params)}")`; ключи JSON сортируются рекурсивно, множества/типы нормализуются |
| Индивидуальный TTL | `BaseConnector.cache_ttl_seconds`; у `exchange_rates` — `EXCHANGE_RATES_TTL_SECONDS`, у `slow_repository` — `SLOW_REPOSITORY_TTL_SECONDS`, иначе `DEFAULT_CACHE_TTL_SECONDS` |
| Автоочистка | TTL-индекс MongoDB `expireAfterSeconds=0` по полю `purge_at` создаётся в `database.ensure_indexes()` при старте |
| Почему не TTL по `expires_at` | Протухшую запись нужно сохранить для fallback. Приложение проверяет свежесть по `expires_at`, а Mongo удаляет документ только когда наступит `purge_at = expires_at + STALE_CACHE_RETENTION_SECONDS`. TTL-монитор Mongo работает с периодичностью ~60 c, поэтому удаление не мгновенно |
| Fallback | При ошибке/таймауте/HTTP-ошибке upstream возвращается последняя найденная запись с `source="stale_cache"`; если записи нет — `502` в той же обертке |
| `force_refresh` | Пропускает свежий кэш, но всё равно читает запись для возможного fallback |
| Конкурентные refresh | Per-key `asyncio.Lock` в `QueryService` (один worker uvicorn в Dockerfile) |
| Отказ кэша | Не ломает успешный live-ответ: пишем warning и возвращаем `live` с `cached_at: null` |

### Единый формат ответа

```json
{
  "module": "slow_repository",
  "source": "live | cache | stale_cache",
  "cached_at": "2026-11-25T20:54:12.123Z",
  "data": { "...": "..." }
}
```

* `live` — данные получены из источника; `cached_at` — время записи свежего снимка в кэш (или `null`, если кэш недоступен).
* `cache` — свежая запись, `cached_at` — оригинальная метка записи.
* `stale_cache` — источник не ответил, отдан кэш (в том числе уже истёкший); `cached_at` — оригинальная метка.
* `none` — техническое значение только для ошибок/служебных ответов без источника данных; `data` содержит `{code, message, details?}`.
* `/health`, `/modules` и `DELETE /cache/{module}` тоже возвращают эту обертку.

Ошибки сохраняют HTTP-коды: `404` неизвестный модуль, `422` невалидные параметры, `502` upstream без кэша, `503` недоступный кэш/БД, `500` внутренняя ошибка.

---

## 3. Конфигурация

Скопируйте `.env.example` в `.env` и при необходимости измените значения:

| Группа | Переменные |
| --- | --- |
| Приложение | `APP_NAME`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `APP_EXTERNAL_PORT`, `LOG_LEVEL` |
| MongoDB | `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD`, `MONGO_INITDB_DATABASE`, `MONGO_DB`, `MONGO_EXTERNAL_PORT`, `MONGODB_URI`, `MONGODB_DATABASE`, `MONGODB_CACHE_COLLECTION`, `MONGO_SERVER_SELECTION_TIMEOUT_MS`, `MONGO_STARTUP_ATTEMPTS`, `MONGO_STARTUP_RETRY_DELAY_SECONDS` |
| Кэш | `DEFAULT_CACHE_TTL_SECONDS`, `STALE_CACHE_RETENTION_SECONDS`, `STALE_CACHE_FALLBACK_ENABLED`, `EXCHANGE_RATES_TTL_SECONDS`, `SLOW_REPOSITORY_TTL_SECONDS` |
| HTTP/коннекторы | `HTTP_TIMEOUT_SECONDS`, `HTTP_CONNECT_TIMEOUT_SECONDS`, `HTTP_MAX_CONNECTIONS`, `HTTP_MAX_KEEPALIVE_CONNECTIONS`, `EXCHANGE_RATES_BASE_URL`, `SLOW_CONNECTOR_DELAY_SECONDS`, `ENABLE_FAILURE_SIMULATION` |

`docker-compose.yml` переопределяет `MONGODB_URI` на внутренний хост `mongo:27017`; значение из `.env` с `127.0.0.1` используется при запуске `uvicorn` вне Docker. Если пароль содержит `@`, `:`, `/` и т.п., закодируйте его в URI (percent-encoding) или используйте безопасные символы.

> `ENABLE_FAILURE_SIMULATION=true` включает debug-флаг `?simulate_outage=true` для демонстрации `stale_cache`. В проде выставьте `false`.

---

## 4. Запуск в Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

Compose поднимает `mongo:7.0` с `healthcheck` и volume `mongo_data:/data/db`, дожидается готовности БД и только затем стартует API. Оба сервиса находятся в сети `gateway_net`; `/api/v1/health` используется как healthcheck контейнера API. Swagger UI: <http://localhost:8000/docs>.

Остановка без удаления данных:

```bash
docker compose down
```

Полная очистка кэша вручную (при необходимости):

```bash
docker compose exec mongo mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --eval 'db.getSiblingDB("api_gateway").response_cache.deleteMany({})'
```

---

## 5. Проверка: первый долгий запрос и мгновенный кэш

`slow_repository` по умолчанию выполняет `asyncio.sleep(2)`, поэтому разница видна без инструментов.

```bash
# 1) Первый запрос: ~2 секунды, source = live
curl -s -o /tmp/first.json -w 'first  = %{time_total}s\n' \
  'http://localhost:8000/api/v1/query/slow_repository?tenant_id=acme&limit=3'

# 2) Повтор: миллисекунды, source = cache
curl -s -o /tmp/second.json -w 'second = %{time_total}s\n' \
  'http://localhost:8000/api/v1/query/slow_repository?tenant_id=acme&limit=3'

jq '{source, cached_at, orders: .data.summary.orders_count}' /tmp/first.json /tmp/second.json
```

Ожидаемо: `first ≈ 2.0s`, `second ≈ 0.01s`, `cached_at` одинаковый во втором ответе.

Дополнительные проверки:

```bash
# Реальный публичный REST API (курсы валют), затем ответ из кэша
curl -s 'http://localhost:8000/api/v1/query/exchange_rates?base=USD&symbols=EUR,RUB' | jq
curl -s 'http://localhost:8000/api/v1/query/exchange_rates?base=USD&symbols=EUR,RUB' | jq '.source'

# Принудительное обновление через POST + JSON-параметры
curl -s -X POST 'http://localhost:8000/api/v1/query/slow_repository?force_refresh=true' \
  -H 'Content-Type: application/json' \
  -d '{"params":{"tenant_id":"acme","limit":3}}' | jq '{source, cached_at}'

# Демонстрация fallback: эмулируем отказ upstream при наличии кэша
curl -s 'http://localhost:8000/api/v1/query/slow_repository?tenant_id=acme&limit=3&force_refresh=true&simulate_outage=true' \
  | jq '{source, cached_at}'
# → {"source": "stale_cache", ...}

# Список автоматически найденных модулей и их TTL/JSON-схемы параметров
curl -s 'http://localhost:8000/api/v1/modules' | jq

# Инвалидация конкретного ключа / всего модуля
curl -s -X DELETE 'http://localhost:8000/api/v1/cache/slow_repository' \
  -H 'Content-Type: application/json' \
  -d '{"params":{"tenant_id":"acme","limit":3}}' | jq

# Healthcheck
curl -s 'http://localhost:8000/api/v1/health' | jq
```

Для проверки именно **истёкшего** кэша уменьшите `EXCHANGE_RATES_TTL_SECONDS=10` в `.env`, перезапустите `docker compose up -d --force-recreate api`, сделайте запрос, подождите 11 секунд и повторите с `&simulate_outage=true` — ответ будет `stale_cache` с прежним `cached_at`.

---

## 6. Локальная разработка и тесты

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Тесты не требуют Docker: `mongomock_motor` заменяет `AsyncIOMotorClient`, а `httpx.MockTransport` — внешний API. Покрыты: детерминированность ключа, `live → cache → force_refresh`, истечение TTL и `stale_cache`, ошибка upstream без кэша, единый формат ошибок, инвалидация, `/health` и `/modules`.

---

## 7. Продакшн-заметки

1. Смените `MONGO_INITDB_ROOT_PASSWORD`, не коммитьте `.env`, выставьте `ENABLE_FAILURE_SIMULATION=false`.
2. В Dockerfile намеренно `--workers 1`: per-key блокировки живут в процессе. При N worker'ах замените их на распределённую блокировку (Redis/MongoDB) или уберите коллапсирование refresh.
3. TTL-индекс удаляет документы по `purge_at` с задержкой монитора MongoDB; `STALE_CACHE_RETENTION_SECONDS` задаёт окно доступности протухших данных.
4. Для внешних API с ключами храните секреты в vault/secret manager и добавляйте их в `Settings` + `.env.example` без значений.
5. `docker-compose.yml` публикует MongoDB наружу для удобства отладки; в проде уберите `ports` у `mongo` и оставьте только внутреннюю сеть.
