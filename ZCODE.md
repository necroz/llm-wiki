# ZCode → llm-wiki: рабочий план интеграции

Рабочая дока для нашей разработки. Не часть апстрима, в upstream не отправлять.

## 1. Цель

Собрать из истории сессий ZCode локальную базу знаний: экспорт из `db.sqlite` → markdown → статический HTML-сайт + MCP. Обновление автоматическое, без ручных запусков.

## 2. Базовая точка

| Параметр | Значение |
|---|---|
| Путь | `~/Projects/llm-wiki` |
| Версия | 1.3.82 |
| Коммит | `b1088890` (2026-06-14) |
| История | полная, 452 коммита, теги v1.3.78 … v1.3.82 |
| Python | >= 3.9 (`pyproject.toml:10`) |
| Зависимости | `markdown>=3.9` (`pyproject.toml:50`); MCP-сервер — чистый stdlib |

### 2.1 Ремоуты

| Remote | URL | Назначение |
|---|---|---|
| `origin` | `https://github.com/necroz/llm-wiki.git` | свой GitHub-форк, сюда идут push |
| `local` | `~/Projects/llm-wiki-fork.git` | локальный bare-репозиторий, резервная копия |
| `upstream` | `https://github.com/Pratiyush/llm-wiki.git` | апстрим, push отключён (`no_push`) |

Форк `necroz/llm-wiki` создан; в него запушены `master`, `zcode-integration` и все теги. Апстрим обновляется через `git fetch upstream`, свои изменения уходят в `origin`.

Подготовка: `python3 -m venv .venv && . .venv/bin/activate && pip install -e .`, затем `python3 -m llmwiki init`.

Полезные команды: `llmwiki sync` (конверт + авто-build), `llmwiki all` (build → graph → export all → lint), `llmwiki serve` (просмотр сайта), `llmwiki adapters` (какие источники активны).

## 3. Контракт адаптера

Новый адаптер — это два файла. CLI, `convert.py` и `build.py` править не нужно: `convert_all` работает generic-ом по реестру.

1. `llmwiki/adapters/zcode.py` — класс с декоратором `@register("zcode")`:
   - `session_store_path` (`adapters/base.py:46`) → `~/.llmwiki/zcode`;
   - `is_available()` — каталог существует и не пуст;
   - `discover_sessions()` (`adapters/base.py:118`) — наследуется, rglob `*.jsonl`;
   - `normalize_records()` (`adapters/base.py:174`) — не нужен, если экспортёр сразу пишет нужный формат.
2. `llmwiki/adapters/__init__.py:97` — добавить `from llmwiki.adapters import zcode`.

**Регистрировать в core, не в contrib.** `discover_adapters()` (`adapters/__init__.py:97`) импортирует только `claude_code` и `codex_cli`. `discover_contrib()` (там же, `:103`) в рантайме не вызывается ниоткуда, кроме тестов. Из-за этого семь contrib-адаптеров (`CONTRIB_ADAPTERS`, `:35` — chatgpt, copilot_chat, copilot_cli, cursor, gemini_cli, obsidian, opencode) в CLI неактивны. Проверено запуском: `llmwiki adapters` печатает только два.

Формат записей, который ждёт пайплайн (Claude-style dicts, читаются в `convert.py`):

```json
{"type":"init","sessionId":"...","cwd":"...","timestamp":"2026-10-20T10:00:00Z"}
{"type":"user","timestamp":"...","sessionId":"...","cwd":"...","message":{"role":"user","content":"текст запроса"}}
{"type":"assistant","timestamp":"...","message":{"role":"assistant","content":[{"type":"text","text":"..."}],"model":"...","usage":{"input_tokens":0,"output_tokens":0}}}
```

`type` ∈ `user` | `assistant` | `init`. Поле `content` — строка либо список блоков `text` / `tool_use` / `tool_result`.

Чек-лист нового адаптера в апстриме: `docs/adapter-authoring.md`.

## 4. Что читаем из ZCode

Каноническое хранилище: `~/.zcode/cli/db/db.sqlite` (2.23 ГБ, SQLite, без шифрования).

| Таблица | Строк | Что берём |
|---|---|---|
| `session` | 1 324 | id, title, directory, task_type, parent_id, time_created, time_updated |
| `message` | 65 196 | `data` JSON: role, time.created, modelId, parentID, path.cwd, tokens |
| `part` | 257 010 | `data` JSON: type, плюс text / tool / file |

Связи: `part.message_id` → `message.id`, `message.session_id` → `session.id`, порядок внутри сообщения по `sequence`.

Значения `part.type`: `text`, `reasoning`, `tool`, `step-start`, `step-finish`, `timeline`, `compaction`, `file`. Для tool-партов в `data` лежат `tool`, `callID`, `state.input`, `state.output`.

Корпус на 2026-10-26: 728 интерактивных сессий и 596 субагентских; 56.9k сообщений assistant и 8.3k user; парты — 69k tool, 39k text, 37k reasoning. Проекты: larisa-ngdu 557, neuromerch_project 395, workspace/default 182, audio-transcript 105. По месяцам: июль 227, август 228, сентябрь 531, октябрь 338.

Дополнительные источники:

- `~/.zcode/cli/agents/<sess>/agent_*/transcript.jsonl` — 216 сессий с транскриптами субагентов, событийная схема (`turn_started`, `model_request`, …). Формат JSONL, но схема другая, поэтому либо отдельный адаптер, либо конвертация внутри экспортёра.
- `~/.zcode/cli/rollout/model-io-*.jsonl` — сырой ввод-вывод моделей, не диалоги. Игнорировать.

Подключение к БД — только read-only и с таймаутом:

```python
sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
```

`immutable=1` не использовать: база живая, есть WAL, иначе не увидите свежих записей.

## 5. План работ

1. `scripts/zcode_export.py` — экспортёр. Читает БД, пишет по одному `.jsonl` на сессию в `~/.llmwiki/zcode/<project-slug>/<session-id>.jsonl`. Идемпотентность — по `session.time_updated` в собственном state-файле. Флаги: `--since YYYY-MM-DD`, `--project <substr>`, `--include-subagents`, `--dry-run`.
2. `llmwiki/adapters/zcode.py` плюс строка импорта на `:97`.
3. Конфиг `examples/sessions_config.json`: `zcode.enabled: true` (верхний уровень; вложенный `adapters` — только для per-adapter roots). `redaction.real_username` оставить пустым: `load_config()` сам подставляет `$USER`.
4. Первый прогон на одном проекте: `zcode_export --project neuromerch_project`, затем `llmwiki sync --adapter zcode --project neuromerch_project`.
5. Проверить, что `llmwiki adapters` показывает zcode как `present: yes`, `active: yes`.

Статус: шаги 1–5 выполнены. Экспортёр — `scripts/zcode_export.py`, адаптер — `llmwiki/adapters/zcode.py`. Первый прогон: 178 сессий, 2 проекта, `site/` собран.

## 6. Обязательные фильтры

Без них вики засорится служебным шумом:

- `session.task_type = 'subagent_child'` — 596 сессий, пропускать по умолчанию, включать флагом.
- `message.data.metadata.visibility = 'model-only'` — служебные сообщения.
- `message.data.metadata.originMeta.source = 'background_task'` — технические task-notification.
- `part.type = 'reasoning'` — 37k партов, выкидывать.
- `part.type = 'tool'` — сворачивать в краткое summary, образец — `summarize_tool_use` (`convert.py:896`).
- `step-start`, `step-finish`, `timeline`, `compaction` — служебные, выкидывать.

## 7. Автообновление

У самого llm-wiki watch-режим удалён в v1.2.0, авторы рекомендуют внешний планировщик. Три уровня дополняют друг друга.

1. **Stop-hook ZCode** — обновление после каждой сессии. Конфиг `~/.zcode/cli/config.json` → `hooks`; нужен `"enabled": true`, иначе файловые хуки не запускаются. Событий ровно семь: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`. Команду отцеплять через `nohup … &`, потому что флаг `async` в хуках сейчас не работает и сессия будет ждать завершения sync.
2. **systemd --user timer** — ночной прогон `zcode_export && llmwiki sync`. Ноль токенов, независимость от клиента.
3. **Автоматизация ZCode по расписанию** — нативная, но запускается как сессия агента, то есть тратит токены.

Плюс **MCP**: добавить сервер в `~/.zcode/cli/config.json` → `mcp.servers`:

```json
{"type":"stdio","command":"python3","args":["-m","llmwiki.mcp"],"cwd":"/home/ilashkevich/Projects/llm-wiki"}
```

Это даёт 12 инструментов (`mcp/server.py:45`), включая `wiki_sync`, прямо в сессии. Поле `cwd` обязательно: пути внутри проекта считаются от корня репозитория.

`llmwiki sync` сам вызывает build (`cli.py:172`), поэтому одной команды хватает для актуального `site/`.

## 8. Риски

- **Пути жёстко привязаны к корню репо**: `DEFAULT_CONFIG_FILE` = `examples/sessions_config.json` (`convert.py:25`), `DEFAULT_STATE_FILE` = `.llmwiki-state.json` (`:24`), `DEFAULT_OUT_DIR` = `raw/sessions` (`:26`). Репозиторий не перемещать либо патчить эти константы.
- **Схема БД ZCode — внутренний контракт.** В `schema_migration` уже 24 миграции. Экспортёр должен проверять наличие колонок и падать с понятной ошибкой, а не молча терять данные.
- **Инкрементальность в llm-wiki построена на mtime файла** (`convert.py:1461-1516`, ключ `adapter::relpath`). Файл на 2.2 ГБ меняется постоянно, поэтому инкрементальность живёт в экспортёре, а llm-wiki видит спокойный каталог `.jsonl`.
- **Объём первого прогона**: 728 сессий и 83k сообщений. Начинать с `--since` и одного проекта.
- Redaction закрывает 17 типов ключей (`convert.py:543`) и имя пользователя, но не пути и не содержимое tool-output. Проверять вывод до публикации.

## 9. Чек-лист приёмки

- [x] `llmwiki adapters` показывает zcode: `present: yes`, `active: yes`
- [x] повторный `zcode_export` без изменений не перезаписывает файлы
- [x] в `raw/sessions/` нет субагентских сессий без явного флага
- [x] `llmwiki sync` создаёт `site/`, сайт открывается через `llmwiki serve`
- [x] в экспорте нет reasoning-партов и task-notification
- [x] поиск по `raw/` не находит реальных API-ключей (0 совпадений; имя пользователя остаётся в выводе `ls`/shell-промптов — известное ограничение §8)
- [ ] MCP-сервер подключается в ZCode и отвечает на `wiki_query`

## 10. Грабли апстрима (не наши)

- Документация обещает загрузку contrib-адаптеров по требованию через `--adapter` — не работает (см. §3).
- README упоминает `llmwiki mcp start` — такой подкоманды нет, только `python3 -m llmwiki.mcp`.
- Cursor-адаптер перечисляет `state.vscdb` в паттернах, но SQLite-парсер не написан.
- Жизненный цикл страниц — пять состояний (`draft → reviewed → verified → stale → archived`), реализация в `llmwiki/lifecycle.py`. Авто: lint прошёл → `reviewed`; confidence < 0.5 или 90 дней без обновления → `stale`.