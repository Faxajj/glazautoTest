"""
Одноразовый скрипт для безопасного сброса БД аккаунтов и добавления Personal Pay.

Секреты НЕ хранятся в файле. Перед запуском установите env-переменные:
  PP_DEVICE_ID
  PP_AUTH_TOKEN
  PP_EMAIL (опционально, default: personalpay@example.com)

Пример:
  PP_DEVICE_ID=... PP_AUTH_TOKEN=... PP_EMAIL=user@example.com python scripts/reset_and_add_pp.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import add_account, delete_account, list_accounts, update_account
from app.drivers.personalpay import get_balance


def _required_env(name: str) -> str:
    val = (os.getenv(name) or "").strip()
    if not val:
        raise RuntimeError(f"Не задана переменная окружения: {name}")
    return val


def main():
    device_id = _required_env("PP_DEVICE_ID")
    auth_token = _required_env("PP_AUTH_TOKEN")
    email = (os.getenv("PP_EMAIL") or "personalpay@example.com").strip()

    credentials = {
        "device_id": device_id,
        "auth_token": auth_token,
    }

    accounts = list_accounts()
    for acc in accounts:
        delete_account(acc["id"])
        print(f"Удалён аккаунт: {acc['label']} ({acc['bank_type']})")

    new_id = add_account("personalpay", email, credentials)
    print(f"Добавлен аккаунт id={new_id}, пока название: {email}")

    try:
        balance_info = get_balance(credentials)
        cvu = (balance_info.get("cvu_number") or "").strip()
        if cvu:
            label = f"{cvu} — {email}"
            update_account(new_id, label=label)
            print(f"Получен CVU, название обновлено: {label}")
        else:
            print("CVU в ответе не найден, название оставлено:", email)
    except Exception as e:
        print(f"Запрос баланса/CVU не выполнен (токен или сеть): {e}")
        print("Название оставлено:", email)

    print("Готово. Открой дашборд и выбери аккаунт.")


if __name__ == "__main__":
    main()
