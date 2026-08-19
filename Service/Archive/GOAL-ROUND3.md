# GOAL ROUND 3 — grok_delegate: переезд сюда + фикс R1–R5

Самодостаточная спека. Всё нужное — здесь; лезть в другие репозитории не требуется.

**Рабочая директория:** `C:\Users\<USER>\Documents\Projects\MCP\Grok CLI` (этот проект).
**Источник кода:** `C:\Users\<USER>\Documents\Projects\pcp-lanes\grokd-delegate-mcp\tools\grok-delegate\`
(файлы: `guard.py`, `runner.py`, `audit.py`, `server.py`, `__init__.py`, `test_grok_delegate.py`, `README.md`).

Что это: dev-only локальный MCP-сервер `grok_delegate` — принимает кодинг-цель и запускает локальный
`grok` headless в **изолированном git worktree** на ветке `grok/*`, возвращая ветку + diffstat для
ревью интегратором. Без auto-merge, без push, без `--always-approve`.

## Обязательно: ровно два содержательных коммита

Проект ещё не под git. Сначала `git init`, затем:

1. **Commit 1 — relocate as-is.** Перенос без правок логики. Раскладку можно сменить на нормальную
   пакетную (`grok_delegate/` вместо `grok-delegate/` — дефис не импортируется; тесты в `tests/`), но
   **содержимое файлов побайтово идентично источнику**. Интегратор сверит sha256. Никаких
   «заодно поправил» в этом коммите.
2. **Commit 2 — fix R1–R5.** Только исправления ниже + технические правки импортов, которых
   потребовал переезд.

Разделение обязательно: иначе нельзя доказать, что при переносе ничего не протащили.

## R1 — CRITICAL: headless не запускается вообще

`guard.py:build_grok_argv` кладёт цель **голым позиционалом**. По справке это «Initial prompt for the
**interactive session**» → запускается TUI, а не headless. Под `capture_output` без TTY это зависнет
до таймаута 900 с. Тесты не поймали, т.к. subprocess замокан.

Починка: перейти на реальный headless-интерфейс — `-p/--single <PROMPT>`, либо `--prompt-file`, либо
подкоманда `agent` (её справка: «Run Grok without the interactive UI»; есть `stdio`/`headless`/`serve`).
**Проверь справку бинарника сам** (`grok --help`, `grok agent --help`), не угадывай. Добавь тест: argv
обязан содержать headless-флаг или подкоманду.

## R2 — CRITICAL: permission-профиль фиктивен

`guard.py:_EXECUTE_ALLOW` разрешает `Bash(python*)`, `Bash(pytest*)`, `Bash(npm*)` — произвольный
интерпретатор. Это обнуляет **все** deny разом:
`python -c "subprocess.run(['git','push'])"` обходит запрет push; чтение `~/.grok/auth.json` обходит
запрет на секреты; `shutil.rmtree` обходит запрет деструктива; `pytest` исполняет произвольный
`conftest.py`.

Починка: убрать интерпретаторы из allow — либо узкий allowlist конкретных неинтерпретирующих команд,
либо честно записать в README и EVIDENCE, что shell **не** guarded (и тогда убрать соответствующие
claim-ы). Молча оставить как есть нельзя.

## R3 — HIGH: cwd-escape не закрыт на Windows

Deny `Write(//**)` / `Edit(//**)` / `Read(//**)` бьёт по UNC-путям, а на Windows абсолютный путь —
`C:\...`. Относительный traversal `../../` не запрещён вообще, при этом `Write(**)` / `Edit(**)`
разрешены. Граница «не выходить за cwd» профилем не обеспечена.

Починка: закрыть реально. Есть `--permission-mode <MODE>` со значениями
`default | acceptEdits | auto | dontAsk | bypassPermissions | plan` — выясни, применяются ли
`--allow`/`--deny` без него вообще, и выбери корректный режим (**никогда** `bypassPermissions`).
Паттерны привести к реальным путям хоста. Если confinement даёт сам `--cwd` — докажи это, а не
предположи.

## R4 — MEDIUM: тесты-театр и завышенные claim-ы

`test_vector_destructive_shell_closed_by_deny` ассертит `"rm -rf" in deny`;
`test_vector_escape_cwd_closed_by_profile` — наличие подстроки `//**`. Это проверка **наличия строки**,
а не запрета. Хелперы `profile_denies_push/merge/cwd_escape` — такой же string-match.

Починка: адверсариальные тесты должны проверять поведение (через `build_grok_argv` / профиль / runner),
а таблицу «Bypass attempts» привести в соответствие с тем, что **реально** закрыто. Не заявляй закрытым
то, что открыто, — честное «не закрыто» лучше.

## R5 — MEDIUM: недекларированный `grok_bin`

`server.py:handle_tool_call` читает `args.get("grok_bin")`, которого **нет** в `_INPUT_SCHEMA` →
клиент может подсунуть произвольный исполняемый файл. Вместе с клиентскими `repo_root`/`lanes_parent`
проверка `is_path_inside` сверяется с *присланным* корнем.

Починка: убрать `grok_bin` из клиентского входа (оставить env/конфиг) либо внести в схему с валидацией;
не доверять клиентским `repo_root`/`lanes_parent` слепо.

## Границы (остаются в силе после переезда)

- **B1** worktree-изоляция обязательна; отказ на lane `dev`/`master`/`main` и на target внутри рабочего
  дерева репо.
- **B2** никогда `--always-approve`; только guarded allow/deny.
- **B3** fail-closed на отсутствие `grok`/git, грязный/недостижимый base.
- **B4** без auto-merge и без push; merge — зона интегратора.
- **B5** bounded `max_turns` (hard cap) + wall-clock timeout + ограничение размера выхлопа.
- **B6** audit без секретов: не логировать содержимое `~/.grok`, полный дифф, сырой текст цели.
- **B9** секретов в коде и argv нет; `auth.json` не читаем.

## Не трогать

`.mcp.json`, `README.md`, `.claude/settings.json` — конфигурация, поставленная интегратором.
**Не** прописывай `grok_delegate` в `.mcp.json`: подключение живого делегирующего сервера — шаг
интегратора **после** приёмки R1–R5 (fail-closed).

В репозиторий `Phone Control Plane` из этого раунда **ничего не коммить**.

## Гейты и DONE

1. `py -3 -m pytest tests -q` — зелёно;
2. `py -3 -m py_compile` по всем модулям — без ошибок;
3. `git log --oneline` — ровно два содержательных коммита в описанном порядке;
4. `EVIDENCE.md` в проекте: честная таблица «Bypass attempts» (что закрыто тестом/guard с
   `file:symbol`, что осталось не-гарантией), плюс вывод по R3 о `--permission-mode`;
5. R1–R5 закрыты либо явно и честно задокументированы как не-гарантии.
