# Banks Dashboard — несколько аккаунтов

Один дашборд для переключения между аккаунтами **UniversalCoins** и **Personal Pay**. Все данные входа хранятся локально в SQLite.

## Запуск

```powershell
cd C:\Users\Glaz01\banks-dashboard
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

Открой в браузере: **http://127.0.0.1:8015**

## Авторизация в дашборде (без регистрации)

Регистрации пользователей нет. Доступ выдают администраторы заранее через переменные окружения:

- `DASHBOARD_USERS` — список логин/пароль в формате `user1:pass1,user2:pass2`
- `DASHBOARD_AUTH_SECRET` — секрет подписи cookie-сессии
- `DASHBOARD_AUTH_SESSION_HOURS` — время жизни сессии в часах (по умолчанию `12`)
- `DASHBOARD_AUTH_COOKIE_SECURE` — выставить `1`, если сайт работает по HTTPS (Secure cookie)

Пример:

```bash
export DASHBOARD_USERS="admin:StrongPass123,operator:AnotherPass456"
export DASHBOARD_AUTH_SECRET="change-this-secret"
export DASHBOARD_AUTH_SESSION_HOURS="24"
export DASHBOARD_AUTH_COOKIE_SECURE="0"  # для локального HTTP, для HTTPS -> 1
uvicorn app.main:app --host 0.0.0.0 --port 8015
```

## Как пользоваться

1. **Добавить аккаунт** — кнопка «+ Добавить аккаунт». Выбери банк (UniversalCoins или Personal Pay), введи название и **Credentials (JSON)**.
2. **Credentials** — скопируй из своего `.env` или из перехвата (HTTP Toolkit). Формат:
   - **UniversalCoins:** `username`, `password`, `pin`, опционально `user_id`, `fcm_token`, `base_url`
   - **Personal Pay:** `username`, `password`, `device_id`, `push_device_token`; или один `auth_token` (JWT), если вход по ссылке из почты
3. В боковой панели выбираешь аккаунт — справа отображаются баланс и форма вывода для этого банка.
4. Переключаться между аккаунтами можно в любой момент.

Данные хранятся в файле `accounts.db` в папке проекта. Не передавай этот файл и бэкапы третьим лицам.

- **Баланс:** на странице аккаунта есть кнопка «↻ Обновить» и автообновление раз в 30 сек.
- Если Personal Pay отдаёт 403 — см. **docs/PERSONALPAY_403_FIX.md**.


## Автовывод

В форме вывода можно включить режим автовывода:
- укажи `Сумма одной выплаты` (например, 400000),
- укажи `Автовывод: общий лимит` (например, 4000000),
- опционально укажи `Автовывод: сумма одной части` (если пусто — берётся сумма одной выплаты).

Система будет отправлять выплаты частями на один и тот же CVU/alias, пока суммарно не достигнет заданного лимита.


## Безопасность и секреты

- Не коммитьте токены/пароли/прокси в репозиторий.
- Храните секреты в переменных окружения и/или `.env` (локально, без git).
- Для продакшена при включённой авторизации обязательно задайте `DASHBOARD_AUTH_SECRET`.

### Одноразовый скрипт reset_and_add_pp.py

Скрипт `scripts/reset_and_add_pp.py` читает секреты только из env:

```bash
PP_DEVICE_ID="..." PP_AUTH_TOKEN="..." PP_EMAIL="user@example.com" python scripts/reset_and_add_pp.py
```


### Проверка merge-конфликтов

Перед коммитом можно запустить:

```bash
python scripts/check_conflict_markers.py
```

Скрипт проверяет отслеживаемые файлы и падает, если найдены строки вида `<<<<<<<`, `=======`, `>>>>>>>`.
