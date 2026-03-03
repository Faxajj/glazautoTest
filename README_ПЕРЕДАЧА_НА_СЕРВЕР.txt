Папка glazauto — полный комплект для выкладки Banks Dashboard на сервер
================================================================================

Содержимое:
- app/          — приложение (main.py, database.py, шаблоны, стили, драйверы Personal Pay и UniversalCoins)
- docs/         — справки (PERSONALPAY_403_FIX и т.д.)
- scripts/      — утилиты при необходимости
- requirements.txt, run_server.sh — зависимости и запуск

Инструкции по выкладке:
- VPS_SETUP_glazauto.md     — настройка VPS (Nginx, systemd, HTTPS)
- СЕРВЕР_И_ДОМЕН.md         — куда ставить, своя ссылка (VPS + домен)
- DEPLOY.md                 — общая выкладка
- REG_RU_glazauto_pro.md    — вариант для Reg.ru
- ИНСТРУКЦИЯ_Один_домен_и_без_ПК.md — работа без ПК, CRON

Что сделано в проекте (изменения и доработки):
- Группы в сайдбаре (GLAZARS, GLAZ3, GLAZ6), сворачивание/разворачивание
- Страница «Список аккаунтов» по группе с таблицей (Открыть, Изменить, Удалить)
- Карточки профилей — светлый вид, без яркой синевы
- Таймауты API: HTTP_TIMEOUT (5, 12) в personalpay.py и universalcoins.py
- Реальный CVU/счёт и алиас из API в блоке «Баланс»
- В истории операций — «От кого» / «Кому» (из details и transactionData API)
- Защита от 500 при открытии аккаунта (JWT, balance_info)

На сервере: создать venv, pip install -r requirements.txt, запустить uvicorn (см. VPS_SETUP_glazauto.md или DEPLOY.md).
Базу accounts.db не копировать — она создаётся при первом запуске.
