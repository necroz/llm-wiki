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

**Регистрировать в core, не в contrib.** `discover_adapters()` (`adapters/__init__.py:95`) теперь импортирует `claude_code`, `codex_cli` и `zcode`. `discover_contrib()` (там же, `:104`) в рантайме не вызывается ниоткуда, кроме тестов, поэтому семь contrib-адаптеров (`CONTRIB_ADAPTERS`, `:35` — chatgpt, copilot_chat, copilot_cli, cursor, gemini_cli, obsidian, opencode) в CLI неактивны без явного `--adapter`.

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

Корпус проверялся на реальной базе ZCode (сотни интерактивных и субагентских сессий, десятки тысяч партов всех типов); конкретные цифры и названия проектов в репозиторий не выносятся.

Дополнительные источники:

- `~/.zcode/cli/agents/<sess>/agent_*/transcript.jsonl` — 216 сессий с транскриптами субагентов, событийная схема (`turn_started`, `model_request`, …). Формат JSONL, но схема другая, поэтому либо отдельный адаптер, либо конвертация внутри экспортёра.
- `~/.zcode/cli/rollout/model-io-*.jsonl` — сырой ввод-вывод моделей, не диалоги. Игнорировать.

Подключение к БД — только read-only и с таймаутом:

```python
sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
```

`immutable=1` не использовать: база живая, есть WAL, иначе не увидите свежих записей.

## 5. План работ

1. `llmwiki/zcode/export.py` — экспортёр (устанавливается вместе с пакетом). Читает БД, пишет по одному `.jsonl` на сессию в `~/.llmwiki/zcode/<project-slug>/<session-id>.jsonl`. Идемпотентность — по `session.time_updated`. Запуск: `llmwiki zcode export`; флаги `--since`, `--project`, `--project-root`, `--include-subagents`, `--keep-stale`, `--strict`, `--dry-run`, `--db`, `--out-dir`. Полный прогон удаляет JSONL сессий, исчезнувших из БД.
2. `llmwiki/adapters/zcode.py` плюс импорт в `discover_adapters()`.
3. Конфиг `examples/sessions_config.json`: `zcode.enabled: true` (верхний уровень). Машинные переопределения — в gitignored `config.json` / `config.local.json`; их теперь читают все команды, не только `synthesize`.
4. Первый прогон: `llmwiki zcode export --project demo_project`, затем `llmwiki sync --adapter zcode --project demo_project`. `sync` сам обновляет JSONL перед конвертацией.
5. Проверить `llmwiki adapters` и `llmwiki doctor`.

Статус: шаги 1–5 выполнены. Экспортёр — `llmwiki/zcode/export.py`, CLI — `llmwiki zcode export`, совместимый wrapper — `scripts/zcode_export.py`. Полный scope: 790 сессий, 13 проектов после объединения `workspace_draft-*` в `workspace_drafts`; `site/graph.html` — граф сессий и проектов (cluster by project).

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

1. **Stop-hook ZCode** — обновление после каждой сессии. Установка: `llmwiki zcode install --dry-run`, затем `llmwiki zcode install`. Установщик идемпотентно правит `~/.zcode/cli/config.json` → `hooks.events.Stop`, включает `hooks.enabled`, сохраняет существующие хуки. На POSIX команда отцепляется через `nohup … &`; на Windows используется `start "" /B`.
2. **systemd --user timer / launchd / Task Scheduler** — `llmwiki zcode install` пишет unit-файлы или plist, либо печатает команду `schtasks`. Ночной прогон вызывает `llmwiki zcode autosync --build`; это одна переносимая Python-команда, без bash и `flock`.
3. **Автоматизация ZCode по расписанию** — нативная, но запускается как сессия агента, то есть тратит токены.

Ручной прогон:

```bash
llmwiki zcode autosync --build
llmwiki zcode autosync --coalesce --build
```

`autosync` берёт OS-level lock (`llmwiki/lock.py`): POSIX `flock`, Windows `msvcrt`. Если другой прогон уже идёт, команда пишет `skip` в лог и выходит с кодом 0. Флаг `--coalesce` запускает детерминированное слияние no-op сессий без LLM.

Плюс **MCP**: добавить сервер в `~/.zcode/cli/config.json` → `mcp.servers`:

```json
{"type":"stdio","command":"/absolute/path/to/llm-wiki/.venv/bin/python","args":["-m","llmwiki.mcp"],"cwd":"/absolute/path/to/llm-wiki"}
```

Это даёт 12 инструментов (`mcp/server.py:45`), включая `wiki_sync`, прямо в сессии. Поле `cwd` обязательно: пути внутри проекта считаются от корня репозитория. `wiki_sync` вызывает `llmwiki sync`, а тот сам обновляет ZCode JSONL перед конвертацией.

`llmwiki sync` сам вызывает build (`cli.py`), поэтому одной команды хватает для актуального `site/`. Stop-hook передаёт `--no-auto-build` (сайт обновляет ночной таймер с `--build`), чтобы каждая остановка сессии не пересобирала 300 МБ.

Статус: Stop-hook и systemd --user timer настроены (`scripts/zcode_sync_hook.sh`, `llmwiki-zcode.timer`, следующий запуск в 03:30); scope — все проекты. `llmwiki zcode install --dry-run` показывает, что установщик запишет. `llmwiki zcode autosync` — переносимый entry point для hook/таймера. MCP-сервер добавлен в `~/.zcode/cli/config.json` (`.venv/bin/python -m llmwiki.mcp`) и проверен через stdio: `initialize`, 12 инструментов, `wiki_query` по `demo_project`. Экспортёр доступен как `llmwiki zcode export`; `llmwiki sync` вызывает его автоматически. `llmwiki audit` ищет username/секреты в `raw/`, `wiki/`, `site/` перед публикацией. Чтобы текущий клиент ZCode подхватил hook и MCP, нужен перезапуск.

## 8. Риски

- **Пути жёстко привязаны к корню репо**: `DEFAULT_STATE_FILE` = `.llmwiki-state.json` (`convert.py`), `DEFAULT_OUT_DIR` = `raw/sessions`. Конфиг теперь layered: `examples/sessions_config.json` → `config.json` → `config.local.json`, поэтому машинные настройки не нужно править в tracked-файле. Репозиторий по-прежнему не перемещать без пересборки state.
- **Slug проекта**: порядок — `~/Projects`, `~/.zcode/workspace`, `zcode.project_roots` / `--project-root`, затем git-root (`git rev-parse --show-toplevel`). Для не-git каталогов вне известных корней всё ещё используется basename.
- **Hook**: `scripts/zcode_sync_hook.sh` теперь только wrapper над `llmwiki zcode autosync`; lock — OS-level в Python. Windows-установщик пишет `.cmd` и команду `schtasks`, macOS — launchd plist. Bash-обёртка нужна только старым POSIX-конфигам.
- **Схема БД ZCode — внутренний контракт.** В `schema_migration` уже 24 миграции. Экспортёр проверяет обязательные колонки, печатает `schema: N` и продолжает с понятной ошибкой при несовпадении.
- **Удалённые сессии**: полный `llmwiki zcode export` удаляет JSONL для сессий, которых больше нет в БД. `--project`/`--since`/`--keep-stale` отключают pruning, чтобы фильтрованный прогон не удалял чужие файлы.
- **Инкрементальность в llm-wiki построена на mtime файла** (`convert.py:1461-1516`, ключ `adapter::relpath`). Файл на 2.2 ГБ меняется постоянно, поэтому инкрементальность живёт в экспортёре, а llm-wiki видит спокойный каталог `.jsonl`.
- **Объём полного прогона**: на момент прогона 770 сессий / 44 проекта, JSONL 245 МБ, raw 42 МБ, site 299 МБ, 1 м 8 с. После объединения draft workspaces — 790 сессий / 13 проектов, site 300 МБ.
- Redaction закрывает 17 типов ключей (`convert.py`) и имя пользователя, но не все пути и не всё содержимое tool-output. Перед публикацией запускать `llmwiki audit` (username, secret, pattern) и проверять отчёт.
- **Потеря `.llmwiki-state.json`** приводит к повторной конвертации. Если `raw/sessions` уже заполнен, конвертер пишет дубликаты с суффиксом `--<hash>`; после сброса состояния проверять `find raw/sessions -name '*--*'`. Тест `test_sync_status_prints_synthesis_hint` больше не пишет в реальный state. Изменённая сессия при наличии ключа в state обновляет canonical-файл на месте (фикс `convert.py`, тест `test_changed_source_updates_canonical_in_place`).

## 9. Чек-лист приёмки

- [x] `llmwiki adapters` показывает zcode: `present: yes`, `active: yes`
- [x] `llmwiki zcode export` и wrapper `scripts/zcode_export.py` дают одинаковый результат
- [x] `llmwiki zcode autosync` работает на POSIX и Windows (Python lock вместо `flock`)
- [x] `llmwiki zcode install --dry-run` показывает hook + scheduler без записи
- [x] `llmwiki audit` находит username/секреты и поддерживает `--fail-on-findings`
- [x] полный `zcode export` удаляет JSONL сессий, исчезнувших из БД; `--project`/`--keep-stale` не трогают чужие файлы
- [x] `llmwiki synthesize --coalesce-only` работает без LLM backend
- [x] `llmwiki doctor` показывает Python, markdown, `.venv`, конфиг, ZCode DB и JSONL
- [x] повторный `llmwiki zcode export` без изменений не перезаписывает файлы
- [x] `llmwiki sync` сам обновляет JSONL и создаёт `site/`, сайт открывается через `llmwiki serve`
- [x] в `raw/sessions/` нет субагентских сессий без явного флага
- [x] `site/graph.html` — граф сессий и проектов: 790 + 13 узлов, 790 рёбер, 0 orphans, cluster by project; wiki-граф — 35 страниц / 37 рёбер, 9 системных orphans
- [x] в экспорте нет reasoning-партов и task-notification
- [x] поиск по `raw/` не находит реальных API-ключей (0 совпадений на 790 сессиях; имя пользователя остаётся в 412 файлах tool-output — известное ограничение §8)
- [x] MCP-сервер отвечает на `wiki_query` (stdio smoke: `initialize` + 12 tools; для загрузки в ZCode нужен перезапуск клиента)

## 10. Грабли апстрима (не наши)

- Документация обещает загрузку contrib-адаптеров по требованию через `--adapter` — не работает (см. §3).
- README упоминает `llmwiki mcp start` — такой подкоманды нет, только `python3 -m llmwiki.mcp`.
- Cursor-адаптер перечисляет `state.vscdb` в паттернах, но SQLite-парсер не написан.
- Жизненный цикл страниц — пять состояний (`draft → reviewed → verified → stale → archived`), реализация в `llmwiki/lifecycle.py`. Авто: lint прошёл → `reviewed`; confidence < 0.5 или 90 дней без обновления → `stale`.