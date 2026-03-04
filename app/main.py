"""
Единый дашборд банков: несколько аккаунтов, переключение между ними.
"""
import base64
import hashlib
import html
import hmac
import json
import os
import secrets
import time
import traceback
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import (
    add_account as db_add_account,
    accounts_by_window,
    delete_account as db_delete_account,
    get_account,
    init_db,
    list_accounts,
    update_account as db_update_account,
    WINDOWS,
)
from app.drivers import (
    BANK_TYPES,
    create_withdraw as driver_withdraw,
    discover_beneficiary,
    get_balance as driver_balance,
)
from app.drivers.personalpay import (
    get_activities_list as pp_activities_list,
    get_transference_details as pp_transference_details,
)

app = FastAPI(title="Banks Dashboard — несколько аккаунтов")
app.add_middleware(GZipMiddleware, minimum_size=1024)

init_db()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

AUTH_COOKIE_NAME = "dashboard_auth"
AUTH_SESSION_HOURS = int(os.getenv("DASHBOARD_AUTH_SESSION_HOURS", "12"))
AUTH_SECRET = os.getenv("DASHBOARD_AUTH_SECRET", "change-me-in-production")
AUTH_COOKIE_SECURE = os.getenv("DASHBOARD_AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
LOGIN_RATE_LIMIT_WINDOW = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "300"))
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "10"))
AUTO_WITHDRAW_MAX_PARTS = int(os.getenv("AUTO_WITHDRAW_MAX_PARTS", "100"))
LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def _load_auth_users() -> dict:
    """Читает учётки из DASHBOARD_USERS: user:pass,user2:pass2."""
    raw = (os.getenv("DASHBOARD_USERS") or "").strip()
    users = {}
    if not raw:
        return users
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        username, password = item.split(":", 1)
        username = username.strip()
        if username and password:
            users[username] = password
    return users


AUTH_USERS = _load_auth_users()
if AUTH_USERS and AUTH_SECRET == "change-me-in-production":
    raise RuntimeError("DASHBOARD_AUTH_SECRET must be set in production when DASHBOARD_USERS is enabled")


def _is_auth_enabled() -> bool:
    return bool(AUTH_USERS)


def _sign_auth_payload(username: str, expires_at: int) -> str:
    payload = f"{username}:{expires_at}"
    signature = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def _verify_auth_cookie(raw_cookie: str) -> Optional[str]:
    if not raw_cookie:
        return None
    parts = raw_cookie.split(":", 2)
    if len(parts) != 3:
        return None
    username, exp_raw, signature = parts
    if username not in AUTH_USERS:
        return None
    try:
        expires_at = int(exp_raw)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    expected = _sign_auth_payload(username, expires_at).rsplit(":", 1)[1]
    if not hmac.compare_digest(signature, expected):
        return None
    return username


def _get_current_user(request: Request) -> Optional[str]:
    return _verify_auth_cookie(request.cookies.get(AUTH_COOKIE_NAME, ""))


def _require_auth(request: Request):
    user = _get_current_user(request)
    if user:
        return user
    return RedirectResponse(url="/login", status_code=302)


def _tojson(obj, indent=2):
    return json.dumps(obj, indent=indent, ensure_ascii=False)

def _format_amount(value, decimals: int = 2, trim_trailing_zeros: bool = True) -> str:
    """Форматирует число с разделителями разрядов: 130805 -> 130.805, 130805.5 -> 130.805,50."""
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        normalized = raw.replace(" ", "")
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        elif "," in normalized:
            normalized = normalized.replace(",", ".")
        try:
            number = float(normalized)
        except ValueError:
            return raw
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)

    sign = "-" if number < 0 else ""
    number = abs(number)
    formatted = f"{number:,.{decimals}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    if trim_trailing_zeros and "," in formatted:
        formatted = formatted.rstrip("0").rstrip(",")
    return sign + formatted




def _parse_amount_input(value: str) -> Optional[Decimal]:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw.replace(" ", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        number = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
    return number

def _check_login_rate_limit(key: str) -> bool:
    now = time.time()
    attempts = [t for t in LOGIN_ATTEMPTS.get(key, []) if now - t <= LOGIN_RATE_LIMIT_WINDOW]
    LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def _register_login_failure(key: str) -> None:
    attempts = LOGIN_ATTEMPTS.get(key, [])
    attempts.append(time.time())
    LOGIN_ATTEMPTS[key] = attempts


def _clear_login_failures(key: str) -> None:
    LOGIN_ATTEMPTS.pop(key, None)


def _require_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    expected = urlparse(str(request.base_url)).netloc
    if not origin and not referer:
        return True
    for source in (origin, referer):
        if not source:
            continue
        try:
            netloc = urlparse(source).netloc
        except Exception:
            return False
        if netloc and netloc == expected:
            return True
    return False
templates.env.filters["tojson"] = _tojson
templates.env.filters["fmt_amount"] = _format_amount

CONCEPTS_UC = [
    ("VARIOS", "VARIOS (разное)"),
    ("ALQUILER", "ALQUILER (аренда)"),
    ("HONORARIOS", "HONORARIOS (гонорары)"),
    ("COMPRA", "COMPRA (покупка)"),
    ("VENTA", "VENTA (продажа)"),
]


def _extract_activities_raw(data) -> list:
    """Из ответа API activities-list извлекает список операций (разные форматы из логов)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    # Плоский список в ключе
    for key in ("data", "activities", "items", "content", "results", "list", "records"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    # Вложенный: data -> { activities | items | list }
    inner = data.get("data")
    if isinstance(inner, dict):
        for key in ("activities", "items", "list", "content", "results"):
            val = inner.get(key)
            if isinstance(val, list):
                return val
        if isinstance(inner.get("data"), list):
            return inner["data"]
    # JSON:API included
    if isinstance(data.get("included"), list):
        return data["included"]
    return []


def _find_any_in_dict(obj, keys: tuple) -> Optional[object]:
    """Рекурсивно ищет в dict/list первое непустое значение по ключам keys (глубина до 5)."""
    def search(o, depth):
        if depth <= 0:
            return None
        if isinstance(o, dict):
            for k in keys:
                v = o.get(k)
                if v is not None and v != "":
                    return v
            for v in o.values():
                r = search(v, depth - 1)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for item in o:
                r = search(item, depth - 1)
                if r is not None:
                    return r
        return None
    return search(obj, 5)


def _find_in_dict(obj, *keys: str, want_number: bool = False):
    """Рекурсивно ищет в dict/list первое значение по ключам keys (глубина до 5)."""
    def search(o, depth):
        if depth <= 0:
            return None
        if isinstance(o, dict):
            for k in keys:
                v = o.get(k)
                if v is None:
                    continue
                if want_number:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
                elif isinstance(v, str) and v.strip():
                    return v.strip()
            for v in o.values():
                r = search(v, depth - 1)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for item in o:
                r = search(item, depth - 1)
                if r is not None:
                    return r
        return None
    return search(obj, 5)


def _find_in_details(act: dict, *labels: str) -> Optional[str]:
    """Ищет в details/items по label/key одно из labels и возвращает value."""
    if not isinstance(act, dict):
        return None
    details = act.get("details") or act.get("items") or act.get("attributes", {}).get("details") or []
    if not isinstance(details, list):
        return None
    labels_lower = [l.lower() for l in labels]
    for d in details:
        if not isinstance(d, dict):
            continue
        label = (d.get("label") or d.get("key") or "").strip().lower()
        if label in labels_lower:
            val = d.get("value") or d.get("displayValue") or d.get("name")
            if val and str(val).strip():
                return str(val).strip()
    return None


def _get_nested(obj: dict, *path: str) -> Optional[str]:
    """Достаёт значение по цепочке ключей, например _get_nested(act, 'transactionData', 'origin', 'holder')."""
    if not isinstance(obj, dict):
        return None
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
        if obj is None:
            return None
    s = str(obj).strip() if obj is not None else ""
    return s if s else None


def _normalize_activity(act: dict) -> dict:
    """Из сырой операции извлекает: title, amount, date, is_outgoing (приход/вывод), receipt_id."""
    if not isinstance(act, dict):
        return {}
    attrs = act.get("attributes") or act
    aid = act.get("id") or act.get("transactionId") or attrs.get("id") or attrs.get("transactionId")
    title = (
        attrs.get("title")
        or attrs.get("description")
        or act.get("title")
        or act.get("description")
        or str(aid or "Операция")
    )
    receipt_id = _find_32char_hex_id(act)

    # Сумма: amount, monto, value (в т.ч. рекурсивно по всему объекту из логов API)
    amount = attrs.get("amount") or attrs.get("monto") or act.get("amount") or act.get("monto")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = None
    if amount is None:
        amount = _find_in_dict(act, "amount", "monto", "value", "total", "ars", want_number=True)

    # Дата: date, fecha, createdAt, created_at, timestamp (в т.ч. из вложенных объектов)
    date_raw = (
        attrs.get("date")
        or attrs.get("fecha")
        or attrs.get("createdAt")
        or attrs.get("created_at")
        or act.get("date")
        or act.get("fecha")
        or act.get("createdAt")
        or act.get("timestamp")
    )
    if date_raw is None:
        date_raw = _find_any_in_dict(act, ("createdAt", "date", "fecha", "timestamp", "created_at"))
    date_str = None
    if date_raw:
        if isinstance(date_raw, (int, float)):
            from datetime import datetime
            try:
                date_str = datetime.utcfromtimestamp(date_raw / 1000 if date_raw > 1e12 else date_raw).strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = str(date_raw)
        else:
            date_str = str(date_raw)[:19] if date_raw else None

    # Направление: приход или вывод
    tx_type = (attrs.get("transactionType") or attrs.get("type") or act.get("transactionType") or act.get("type") or "").lower()
    title_lower = (title or "").lower()
    is_outgoing = (
        "output" in tx_type
        or "out" in tx_type
        or "outgoing" in tx_type
        or "enviaste" in title_lower
        or "envío" in title_lower
        or "transferencia enviada" in title_lower
    )

    # Объект для парсинга: список может возвращать { "transference": { details, transactionData } }
    inner = act.get("transference") if isinstance(act.get("transference"), dict) else act

    # От кого / Кому: из details (Envía/Recibe), transactionData.origin/destination, верхний уровень
    sender = (
        attrs.get("remitente")
        or attrs.get("sender")
        or attrs.get("originName")
        or attrs.get("senderName")
        or act.get("remitente")
        or act.get("sender")
        or act.get("from")
        or _find_in_details(act, "remitente", "titular", "origen", "sender", "nombre", "envía", "envia")
        or _find_in_details(inner, "remitente", "titular", "origen", "sender", "nombre", "envía", "envia")
        or _get_nested(inner, "transactionData", "origin", "holder")
        or _find_in_dict(act, "remitente", "sender", "originName", "holder", want_number=False)
        or _find_in_dict(inner, "remitente", "sender", "originName", "holder", want_number=False)
    )
    recipient = (
        attrs.get("destinatario")
        or attrs.get("recipient")
        or attrs.get("recipientName")
        or act.get("destinatario")
        or act.get("recipient")
        or act.get("to")
        or _find_in_details(act, "destinatario", "beneficiario", "recipient", "nombre", "recibe")
        or _find_in_details(inner, "destinatario", "beneficiario", "recipient", "nombre", "recibe")
        or _get_nested(inner, "transactionData", "destination", "holder")
        or _get_nested(inner, "transactionData", "destination", "label")
        or _find_in_dict(act, "destinatario", "recipient", "beneficiary", "label", "holder", want_number=False)
        or _find_in_dict(inner, "destinatario", "recipient", "beneficiary", "label", "holder", want_number=False)
    )
    if isinstance(sender, str):
        sender = sender.strip() or None
    if isinstance(recipient, str):
        recipient = recipient.strip() or None

    return {
        "id": aid,
        "title": title,
        "receipt_id": receipt_id,
        "amount": amount,
        "date_str": date_str,
        "is_outgoing": is_outgoing,
        "sender": sender,
        "recipient": recipient,
        "_raw": act,
    }


def _find_32char_hex_id(obj) -> Optional[str]:
    """Рекурсивно ищет в dict/list строку — 32 символа, hex без дефисов (ID операции банка для чека)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and "-" not in v and len(v) == 32 and all(c in "0123456789ABCDEFabcdef" for c in v):
                return v
            found = _find_32char_hex_id(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_32char_hex_id(item)
            if found:
                return found
    return None


def _jwt_expiry(credentials: dict) -> Tuple[bool, Optional[float]]:
    """Если в credentials есть auth_token (JWT), возвращает (истёк_ли, часов_до_истечения или None)."""
    token = (credentials.get("auth_token") or "").strip()
    if token.upper().startswith("BEARER "):
        token = token[7:].strip()
    if not token or not token.startswith("eyJ"):
        return False, None
    parts = token.split(".")
    if len(parts) < 2:
        return False, None
    try:
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return False, None
    exp = payload.get("exp")
    if not exp:
        return False, None
    now = time.time()
    if now >= exp:
        return True, 0.0
    return False, (exp - now) / 3600.0


def _window_name(slug: str) -> str:
    for s, name in WINDOWS:
        if s == slug:
            return name
    return slug


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_auth_enabled() and _get_current_user(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "auth_enabled": _is_auth_enabled(),
            "is_authenticated": False,
        },
    )


@app.post("/login", response_class=RedirectResponse)
async def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    if not _is_auth_enabled():
        return RedirectResponse(url="/", status_code=302)
    if not _require_same_origin(request):
        return RedirectResponse(url="/login?error=csrf", status_code=302)

    login = (username or "").strip()
    remote = request.client.host if request.client and request.client.host else "unknown"
    rate_key = f"{remote}:{login.lower()}"
    if _check_login_rate_limit(rate_key):
        return RedirectResponse(url="/login?error=rate_limit", status_code=302)

    valid_password = AUTH_USERS.get(login)
    if not valid_password or not secrets.compare_digest(valid_password, password or ""):
        _register_login_failure(rate_key)
        return RedirectResponse(url="/login?error=1", status_code=302)

    _clear_login_failures(rate_key)
    expires_at = int(time.time()) + AUTH_SESSION_HOURS * 3600
    token = _sign_auth_payload(login, expires_at)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=AUTH_SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=AUTH_COOKIE_SECURE,
    )
    return response


@app.post("/logout", response_class=RedirectResponse)
async def logout_post(request: Request):
    if not _require_same_origin(request):
        return RedirectResponse(url="/login?error=csrf", status_code=302)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, account_id: Optional[int] = None, window: Optional[str] = None):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    try:
        return await _index_impl(request, account_id, window)
    except Exception as e:
        if APP_DEBUG:
            tb = html.escape(traceback.format_exc())
            body = f'<h1>Ошибка</h1><pre style="white-space:pre-wrap;background:#0f172a;padding:1rem;border-radius:8px;">{tb}</pre>'
        else:
            body = '<h1>Ошибка</h1><p>Внутренняя ошибка сервера. Проверьте логи.</p>'
        return HTMLResponse(
            content=f'<html><body style="font-family:sans-serif;padding:2rem;background:#1e293b;color:#e2e8f0;">{body}<p><a href="/" style="color:#94a3b8;">На главную</a></p></body></html>',
            status_code=500,
        )


async def _index_impl(request: Request, account_id: Optional[int], window: Optional[str]):
    accounts = list_accounts()
    groups = accounts_by_window()
    selected = None
    balance_info = None
    accounts_display = None
    error = None
    window_accounts = []
    window_slug = None
    window_name = None
    if window and window in [w[0] for w in WINDOWS]:
        window_slug = window
        window_name = _window_name(window)
        window_accounts = groups.get(window, [])
    if account_id:
        selected = get_account(account_id)
        if selected:
            try:
                balance_info = driver_balance(selected["bank_type"], selected["credentials"])
                if selected["bank_type"] == "personalpay":
                    accounts_display = balance_info.get("raw_accounts")
                    balance_info = {
                        "balance": balance_info.get("balance", 0),
                        "cvu_number": balance_info.get("cvu_number", ""),
                        "cvu_alias": balance_info.get("cvu_alias", ""),
                    }
                else:
                    balance_info = {
                        "balance": balance_info.get("balance", 0),
                        "cvu_number": balance_info.get("cvu_number", ""),
                        "cvu_alias": balance_info.get("cvu_alias", ""),
                    }
                if balance_info.get("balance") is None:
                    balance_info["balance"] = 0
                else:
                    try:
                        balance_info["balance"] = float(balance_info["balance"])
                    except (TypeError, ValueError):
                        balance_info["balance"] = 0
            except Exception as e:
                balance_info = {"error": str(e)}
                error = str(e)
    token_expired = False
    token_expires_in_hours = None
    if selected and selected.get("credentials") and selected["credentials"].get("auth_token"):
        try:
            token_expired, token_expires_in_hours = _jwt_expiry(selected["credentials"])
        except Exception:
            token_expired, token_expires_in_hours = False, None
    activities_list = []
    if selected and selected["bank_type"] == "personalpay":
        try:
            data = pp_activities_list(selected["credentials"], offset=0, limit=15)
            raw = _extract_activities_raw(data)
            for act in raw if isinstance(raw, list) else []:
                normalized = _normalize_activity(act)
                if normalized:
                    activities_list.append(normalized)
        except Exception:
            pass
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "is_authenticated": bool(_get_current_user(request)),
            "accounts": accounts,
            "groups": groups,
            "window_list": WINDOWS,
            "selected": selected,
            "account_id": account_id,
            "balance_info": balance_info,
            "accounts_display": accounts_display,
            "bank_types": BANK_TYPES,
            "concepts_uc": CONCEPTS_UC,
            "error": error,
            "token_expired": token_expired,
            "token_expires_in_hours": token_expires_in_hours,
            "activities_list": activities_list or [],
            "window_slug": window_slug,
            "window_name": window_name,
            "window_accounts": window_accounts,
            "prefill": {
                "cvu": "",
                "destination": "",
                "amount": "",
                "concept": "VARIOS",
                "comments": "Varios (VAR)",
                "alias": "",
                "document": "",
                "name": "",
                "bank": "",
            },
        },
    )


@app.get("/account/{account_id}/balance")
async def api_balance(request: Request, account_id: int):
    """JSON: актуальный баланс для аккаунта (для кнопки и автообновления)."""
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return {"error": "unauthorized"}
    acc = get_account(account_id)
    if not acc:
        return {"error": "account not found"}
    try:
        balance_info = driver_balance(acc["bank_type"], acc["credentials"])
        if acc["bank_type"] == "personalpay":
            return {
                "balance": balance_info.get("balance", 0),
                "cvu_number": balance_info.get("cvu_number", ""),
                "cvu_alias": balance_info.get("cvu_alias", ""),
            }
        return {
            "balance": balance_info.get("balance", 0),
            "cvu_number": balance_info.get("cvu_number", ""),
            "cvu_alias": balance_info.get("cvu_alias", ""),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/account/{account_id}/withdraw", response_class=HTMLResponse)
async def withdraw(
    request: Request,
    account_id: int,
    cvu: str = Form(""),
    destination: str = Form(""),
    amount: str = Form(...),
    auto_total_amount: str = Form(""),
    auto_chunk_amount: str = Form(""),
    concept: str = Form("VARIOS"),
    comments: str = Form("Varios (VAR)"),
    alias: str = Form(""),
    document: str = Form(""),
    name: str = Form(""),
    bank: str = Form(""),
):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    if not _require_same_origin(request):
        return RedirectResponse(url=f"/?account_id={account_id}&error=csrf", status_code=302)
    acc = get_account(account_id)
    if not acc:
        return RedirectResponse(url="/", status_code=302)

    single_amount = _parse_amount_input(amount)
    if single_amount is None or single_amount <= 0:
        return RedirectResponse(url=f"/?account_id={account_id}&error=invalid_amount", status_code=302)

    total_target = _parse_amount_input(auto_total_amount)
    chunk_amount = _parse_amount_input(auto_chunk_amount)
    if total_target is not None and total_target <= 0:
        return RedirectResponse(url=f"/?account_id={account_id}&error=invalid_auto_total", status_code=302)
    if chunk_amount is not None and chunk_amount <= 0:
        return RedirectResponse(url=f"/?account_id={account_id}&error=invalid_auto_chunk", status_code=302)

    is_auto = total_target is not None
    if is_auto and chunk_amount is None:
        chunk_amount = single_amount
    current_chunk = chunk_amount if is_auto else single_amount
    if not current_chunk or current_chunk <= 0:
        return RedirectResponse(url=f"/?account_id={account_id}&error=invalid_amount", status_code=302)

    dest = (destination or cvu).strip()
    if not dest:
        return RedirectResponse(url=f"/?account_id={account_id}&error=no_destination", status_code=302)

    doc_clean = (document or "").strip().replace("-", "").replace(" ", "")
    if acc["bank_type"] == "universalcoins" and (not doc_clean or len(doc_clean) < 10):
        return RedirectResponse(url=f"/?account_id={account_id}&error=document_required", status_code=302)

    def perform_withdraw(withdraw_amount: float):
        if acc["bank_type"] == "universalcoins":
            return driver_withdraw(
                acc["bank_type"],
                acc["credentials"],
                cvu_recipient=dest,
                amount=withdraw_amount,
                concept=concept,
                alias_recipient=alias.strip() or None,
                document_recipient=doc_clean,
                name_recipient=name.strip() or None,
                bank_recipient=bank.strip() or None,
            )
        return driver_withdraw(
            acc["bank_type"],
            acc["credentials"],
            destination=dest,
            amount=withdraw_amount,
            comments=comments,
        )

    def extract_tid(result: dict) -> Optional[str]:
        if acc["bank_type"] != "personalpay" or not isinstance(result, dict):
            return None
        tid = _find_32char_hex_id(result)
        if tid:
            return tid
        raw = (
            result.get("transactionId")
            or result.get("id")
            or (result.get("transference") or {}).get("id")
            or (result.get("data") or {}).get("transactionId")
        )
        if raw:
            raw = str(raw).strip()
            if "-" not in raw and len(raw) == 32 and all(c in "0123456789ABCDEFabcdef" for c in raw):
                return raw
        return None

    sent_total = Decimal("0")
    sent_count = 0
    last_tid = None
    try:
        if is_auto:
            remaining = total_target
            while remaining and remaining > 0:
                part = min(current_chunk, remaining)
                if sent_count >= AUTO_WITHDRAW_MAX_PARTS:
                    return RedirectResponse(url=f"/?account_id={account_id}&error=auto_parts_limit", status_code=302)
                result = perform_withdraw(float(part))
                sent_total += part
                sent_count += 1
                remaining = remaining - part
                maybe_tid = extract_tid(result)
                if maybe_tid:
                    last_tid = maybe_tid
        else:
            result = perform_withdraw(float(single_amount))
            sent_total = single_amount
            sent_count = 1
            last_tid = extract_tid(result)
    except Exception as e:
        err_msg = str(e)
        if any(x in err_msg.lower() for x in ("rechazad", "rejected", "rechazo", "denied", "denegad")):
            error_param = "rejected_by_bank"
        else:
            error_param = quote(err_msg[:200], safe="")
        return RedirectResponse(url=f"/?account_id={account_id}&error={error_param}", status_code=302)

    if last_tid and not is_auto:
        return RedirectResponse(url=f"/account/{account_id}/receipt?transaction_id={last_tid}&from_withdraw=1", status_code=302)

    done = _format_amount(sent_total)
    return RedirectResponse(
        url=f"/?account_id={account_id}&success=1&auto_done={done}&auto_count={sent_count}",
        status_code=302,
    )


@app.get("/add", response_class=HTMLResponse)
async def add_account_page(request: Request, window: str = ""):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    return templates.TemplateResponse(
        "add_account.html",
        {
            "request": request,
            "is_authenticated": bool(_get_current_user(request)),
            "bank_types": BANK_TYPES,
            "window_list": WINDOWS,
            "preselect_window": window or "glazars",
            "accounts": list_accounts(),
            "groups": accounts_by_window(),
            "account_id": None,
            "selected": None,
            "window_slug": None,
        },
    )




def _proxy_to_url(raw: str, scheme: str = "socks5h") -> str:
    """Нормализует прокси из UI.

    Поддержка:
    - host:port:user:pass -> <scheme>://user:pass@host:port
    - user:pass@host:port -> <scheme>://user:pass@host:port
    - host:port -> <scheme>://host:port
    - готовый URL (socks5://, socks5h://, http://, https://) -> как есть
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low.startswith(("socks5://", "socks5h://", "http://", "https://")):
        return raw
    if "@" in raw:
        return f"{scheme}://{raw}"
    parts = [p.strip() for p in raw.split(":")]
    if len(parts) >= 4:
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        if host and port and user and password:
            return f"{scheme}://{user}:{password}@{host}:{port}"
    if len(parts) == 2 and all(parts):
        return f"{scheme}://{parts[0]}:{parts[1]}"
    return f"{scheme}://{raw}"


def _proxy_from_parts(
    proxy_host: str,
    proxy_port: str,
    proxy_user: str,
    proxy_password: str,
    proxy_type: str,
    proxy_raw: str,
) -> str:
    """Собирает proxy URL из отдельных полей. Если поля пустые — fallback к raw строке."""
    scheme = (proxy_type or "socks5h").strip().lower()
    if scheme not in ("socks5", "socks5h", "http", "https"):
        scheme = "socks5h"

    host = (proxy_host or "").strip()
    port = (proxy_port or "").strip()
    user = (proxy_user or "").strip()
    password = (proxy_password or "").strip()

    if host and port:
        auth = f"{user}:{password}@" if user and password else ""
        return f"{scheme}://{auth}{host}:{port}"

    return _proxy_to_url(proxy_raw, scheme=scheme) if (proxy_raw or "").strip() else ""


def _apply_proxy_to_credentials(proxy_url: str, credentials: dict) -> dict:
    """Добавляет/обновляет proxy-поля в credentials."""
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return credentials
    c = dict(credentials or {})
    c["proxy"] = proxy_url
    c["http_proxy"] = proxy_url
    c["https_proxy"] = proxy_url
    return c


def _proxy_parts_from_credentials(credentials: dict) -> dict:
    """Достаёт proxy fields для формы редактирования из сохранённого URL."""
    c = credentials or {}
    raw = (c.get("https_proxy") or c.get("proxy") or c.get("http_proxy") or "").strip()
    result = {
        "proxy_type": "socks5h",
        "proxy_host": "",
        "proxy_port": "",
        "proxy_user": "",
        "proxy_password": "",
        "proxy_raw": raw,
    }
    if not raw:
        return result

    low = raw.lower()
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        if scheme.lower() in ("socks5", "socks5h", "http", "https"):
            result["proxy_type"] = scheme.lower()
        raw = rest

    auth, hostport = (raw.split("@", 1) + [""])[:2] if "@" in raw else ("", raw)
    if auth and ":" in auth:
        u, p = auth.split(":", 1)
        result["proxy_user"] = u
        result["proxy_password"] = p

    if hostport and ":" in hostport:
        h, pt = hostport.rsplit(":", 1)
        result["proxy_host"] = h
        result["proxy_port"] = pt
    else:
        result["proxy_host"] = hostport
    return result


def _parse_credentials(raw: str) -> dict:
    """Парсит credentials: либо JSON, либо токен (в т.ч. строка вида Authorization: eyJ...)."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Убираем префикс заголовка, если вставили целиком "Authorization: eyJ..." или "Authorization:\neyJ..."
    token = raw.strip()
    for prefix in ("Authorization:", "Authorization：", "authorization:"):
        if token.upper().startswith(prefix.upper()):
            token = token[len(prefix):].strip()
            break
    if not token:
        return {}
    if token.upper().startswith("BEARER "):
        return {"auth_token": token}
    if token.startswith("eyJ"):
        return {"auth_token": "Bearer " + token}
    return {}


@app.post("/add", response_class=RedirectResponse)
async def add_account_post(
    request: Request,
    bank_type: str = Form(...),
    label: str = Form(...),
    credentials_json: str = Form("{}"),
    proxy_type: str = Form("socks5h"),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_user: str = Form(""),
    proxy_password: str = Form(""),
    proxy_raw: str = Form(""),
    window: str = Form("glazars"),
):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    if not _require_same_origin(request):
        return RedirectResponse(url="/add?error=csrf", status_code=302)
    if bank_type not in BANK_TYPES:
        return RedirectResponse(url="/add?error=invalid_bank", status_code=302)
    if window not in (w[0] for w in WINDOWS):
        window = "glazars"
    credentials = _parse_credentials(credentials_json)
    if not credentials and credentials_json.strip():
        return RedirectResponse(url="/add?error=invalid_json", status_code=302)
    if bank_type == "personalpay":
        proxy_url = _proxy_from_parts(
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_user=proxy_user,
            proxy_password=proxy_password,
            proxy_type=proxy_type,
            proxy_raw=proxy_raw,
        )
        credentials = _apply_proxy_to_credentials(proxy_url, credentials)
    if not label.strip():
        label = f"{BANK_TYPES[bank_type]['name']} — {bank_type}"
    new_id = db_add_account(bank_type, label.strip(), credentials, window=window)
    return RedirectResponse(url=f"/?account_id={new_id}", status_code=302)


@app.get("/account/{account_id}/edit", response_class=HTMLResponse)
async def edit_account_page(request: Request, account_id: int):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    acc = get_account(account_id)
    if not acc:
        return RedirectResponse(url="/", status_code=302)
    proxy_parts = _proxy_parts_from_credentials(acc.get("credentials") or {})
    return templates.TemplateResponse(
        "edit_account.html",
        {
            "request": request,
            "is_authenticated": bool(_get_current_user(request)),
            "account": acc,
            "window_list": WINDOWS,
            "accounts": list_accounts(),
            "groups": accounts_by_window(),
            "account_id": None,
            "selected": acc,
            "window_slug": None,
            **proxy_parts,
        },
    )


@app.post("/account/{account_id}/edit", response_class=RedirectResponse)
async def edit_account_post(
    request: Request,
    account_id: int,
    label: str = Form(...),
    credentials_json: str = Form("{}"),
    proxy_type: str = Form("socks5h"),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_user: str = Form(""),
    proxy_password: str = Form(""),
    proxy_raw: str = Form(""),
    window: str = Form(""),
):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    if not _require_same_origin(request):
        return RedirectResponse(url=f"/account/{account_id}/edit?error=csrf", status_code=302)
    acc = get_account(account_id)
    if not acc:
        return RedirectResponse(url="/", status_code=302)
    credentials = _parse_credentials(credentials_json)
    if not credentials and credentials_json.strip():
        return RedirectResponse(url=f"/account/{account_id}/edit?error=invalid_json", status_code=302)
    if acc["bank_type"] == "personalpay":
        proxy_url = _proxy_from_parts(
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_user=proxy_user,
            proxy_password=proxy_password,
            proxy_type=proxy_type,
            proxy_raw=proxy_raw,
        )
        credentials = _apply_proxy_to_credentials(proxy_url, credentials)
    if label.strip():
        db_update_account(account_id, label=label.strip())
    if credentials:
        db_update_account(account_id, credentials=credentials)
    if window and window in (w[0] for w in WINDOWS):
        db_update_account(account_id, window=window)
    return RedirectResponse(url=f"/?account_id={account_id}&success=updated", status_code=302)


@app.get("/account/{account_id}/receipt", response_class=HTMLResponse)
async def receipt(request: Request, account_id: int, transaction_id: str = ""):
    """Детали перевода (чек) Personal Pay по transactionId."""
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    acc = get_account(account_id)
    if not acc or acc["bank_type"] != "personalpay" or not transaction_id.strip():
        return RedirectResponse(url=f"/?account_id={account_id}", status_code=302)
    try:
        data = pp_transference_details(acc["credentials"], transaction_id)
    except Exception as e:
        return templates.TemplateResponse(
            "receipt.html",
            {
                "request": request,
                "is_authenticated": bool(_get_current_user(request)),
                "account": acc,
                "transaction_id": transaction_id,
                "error": str(e),
                "transference": None,
                "groups": accounts_by_window(),
                "window_list": WINDOWS,
                "window_slug": None,
            },
        )
    transference = (data.get("transference") or data) if isinstance(data, dict) else None
    receipt_lines = []
    if transference and isinstance(transference, dict):
        details = transference.get("details") or []
        label_ru = {
            "fecha": "Дата",
            "date": "Дата",
            "monto": "Сумма",
            "amount": "Сумма",
            "estado": "Статус",
            "status": "Статус",
            "remitente": "Отправитель",
            "titular": "Отправитель",
            "origen": "Отправитель",
            "sender": "Отправитель",
            "cuenta origen": "Отправитель",
            "nombre": "Имя получателя",
            "name": "Имя получателя",
            "destinatario": "Получатель",
            "recipient": "Получатель",
            "beneficiario": "Получатель",
            "cuit": "Получатель CUIT",
            "cvu": "Получатель CVU",
            "banco": "Банк получателя",
            "bank": "Банк получателя",
            "banco origen": "Банк отправителя",
            "banco remitente": "Банк отправителя",
            "origen banco": "Банк отправителя",
            "sender bank": "Банк отправителя",
            "banco emisor": "Банк отправителя",
            "id": "ID",
            "balance": "Баланс",
        }
        skip_labels = {"utr"}
        seen_recipient_value = None
        for d in details:
            if not isinstance(d, dict):
                continue
            label = (d.get("label") or d.get("key") or "").strip().lower()
            value = d.get("value") or d.get("displayValue") or ""
            if label in skip_labels:
                continue
            label_show = label_ru.get(label) or (d.get("label") or d.get("key") or "")
            if label_show and label_show.strip().lower() in skip_labels:
                continue
            if not (label_show or value):
                continue
            if label_show == "Получатель" and str(value).strip() == str(seen_recipient_value or "").strip():
                continue
            if label_show == "Имя получателя" and str(value).strip() == str(seen_recipient_value or "").strip():
                continue
            if label_show in ("Получатель", "Имя получателя"):
                seen_recipient_value = value
            receipt_lines.append({"label": label_show or label, "value": value})
        tid = transference.get("id") or transaction_id
        if tid and not any((str(r.get("value") or "").strip() == str(tid).strip() for r in receipt_lines)):
            receipt_lines.append({"label": "ID", "value": tid})
    tx_type = (transference or {}).get("transactionType") or ""
    is_outgoing = "output" in tx_type.lower() or "out" in tx_type.lower() or (transference or {}).get("title", "").lower().startswith("enviaste")
    amount_val = (transference or {}).get("amount")
    amount_num = _parse_amount_input(str(amount_val)) if amount_val is not None else None
    amount_display = f"-{amount_val}" if (is_outgoing and amount_num is not None and amount_num > 0) else (amount_val if amount_val is not None else "")
    return templates.TemplateResponse(
        "receipt.html",
        {
            "request": request,
            "is_authenticated": bool(_get_current_user(request)),
            "account": acc,
            "transaction_id": transaction_id,
            "error": None,
            "transference": transference,
            "receipt_lines": receipt_lines,
            "receipt_title": "Исходящая транзакция" if is_outgoing else (transference or {}).get("title") or "Чек перевода",
            "amount_display": amount_display,
            "is_outgoing": is_outgoing,
            "groups": accounts_by_window(),
            "window_list": WINDOWS,
            "window_slug": None,
        },
    )


@app.post("/account/{account_id}/delete", response_class=RedirectResponse)
async def delete_account(request: Request, account_id: int, redirect_window: Optional[str] = Form(None)):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    if not _require_same_origin(request):
        return RedirectResponse(url=f"/?account_id={account_id}&error=csrf", status_code=302)
    db_delete_account(account_id)
    if redirect_window and redirect_window in [w[0] for w in WINDOWS]:
        return RedirectResponse(url=f"/?window={redirect_window}", status_code=302)
    return RedirectResponse(url="/", status_code=302)


@app.get("/account/{account_id}/discover", response_class=HTMLResponse)
async def discover(request: Request, account_id: int, destination: str = ""):
    auth_check = _require_auth(request)
    if isinstance(auth_check, RedirectResponse):
        return HTMLResponse(content="{}", media_type="application/json")
    acc = get_account(account_id)
    if not acc or acc["bank_type"] != "personalpay" or not destination.strip():
        return HTMLResponse(content="{}", media_type="application/json")
    try:
        data = discover_beneficiary(acc["bank_type"], acc["credentials"], destination)
        return HTMLResponse(content=json.dumps(data, ensure_ascii=False), media_type="application/json")
    except Exception as e:
        return HTMLResponse(content=json.dumps({"error": str(e)}), media_type="application/json")
