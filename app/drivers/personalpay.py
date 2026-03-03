 """
 Personal Pay API — работа по переданным credentials (dict).
 Прод-версия с безопасными таймаутами.
 """
+import re
 import time
 import uuid
 from typing import Optional
 
 import requests
 
 
 HTTP_TIMEOUT = (5, 12)  # (connect, read) — даём API время ответить, без лишнего ожидания
 
 
+def _clean_text(value) -> str:
+    if value is None:
+        return ""
+    s = str(value).replace("\ufeff", "").replace("\r", "").strip()
+    return s
+
+
+def _new_session() -> requests.Session:
+    """Отключаем env-прокси/переменные окружения: на Linux-сервере они могут отличаться от Windows-локалки."""
+    session = requests.Session()
+    session.trust_env = False
+    return session
+
+
+def _normalize_base_url(value: str) -> str:
+    base = _clean_text(value).rstrip("/")
+    if not base:
+        return "https://mobile.prod.personalpay.dev"
+    if base.startswith("//"):
+        return "https:" + base
+    if "://" not in base:
+        return "https://" + base
+    return base
+
+
+def _clean_auth_token(value) -> str:
+    token = _clean_text(value)
+    if not token:
+        return ""
+    for prefix in ("Authorization:", "Authorization：", "authorization:"):
+        if token.upper().startswith(prefix.upper()):
+            token = token[len(prefix):].strip()
+            break
+    if token.upper().startswith("BEARER "):
+        token = token[7:].strip()
+    token = token.strip('"').strip("'").strip()
+    token = re.sub(r"\s+", "", token)
+    return token
+
+
+def _clean_device_id(value) -> str:
+    device_id = _clean_text(value).strip('"').strip("'").strip()
+    return re.sub(r"\s+", "", device_id)
+
+
+def _clean_user_agent(value) -> str:
+    return _clean_text(value).replace("\n", " ").strip()
+
+
+def _first_nonempty_str(*values) -> str:
+    for value in values:
+        if value is None:
+            continue
+        s = _clean_text(value)
+        if s:
+            return s
+    return ""
+
+
+def _device_id_from_paygilant(value: str) -> str:
+    raw = _clean_text(value)
+    if not raw:
+        return ""
+    return raw.split("_", 1)[0].strip()
+
+
 def _norm_creds(creds: dict) -> dict:
-    base = (creds.get("base_url") or "https://mobile.prod.personalpay.dev").strip().rstrip("/")
+    headers = creds.get("headers") if isinstance(creds.get("headers"), dict) else {}
+    push = creds.get("pushNotifications") if isinstance(creds.get("pushNotifications"), dict) else {}
+
+    base = _normalize_base_url(_first_nonempty_str(
+            creds.get("base_url"),
+            creds.get("baseUrl"),
+            creds.get("host"),
+        ))
+
+    auth_token = _clean_auth_token(_first_nonempty_str(
+        creds.get("auth_token"),
+        creds.get("authToken"),
+        creds.get("idToken"),
+        creds.get("id_token"),
+        creds.get("authorization"),
+        headers.get("Authorization"),
+        headers.get("authorization"),
+    ))
+
+    device_id = _clean_device_id(_first_nonempty_str(
+        creds.get("device_id"),
+        creds.get("deviceId"),
+        creds.get("deviceID"),
+        _device_id_from_paygilant(creds.get("x_fraud_paygilant_session_id")),
+        _device_id_from_paygilant(creds.get("x-fraud-paygilant-session-id")),
+        _device_id_from_paygilant(headers.get("x-fraud-paygilant-session-id")),
+        _device_id_from_paygilant(headers.get("X-Fraud-Paygilant-Session-Id")),
+    ))
+
     return {
         "base_url": base,
-        "username": (creds.get("username") or "").strip(),
-        "password": (creds.get("password") or "").strip().strip('"').strip("'"),
-        "device_id": (creds.get("device_id") or "").strip(),
-        "push_device_token": (creds.get("push_device_token") or "").strip(),
-        "auth_token": (creds.get("auth_token") or "").strip(),
-        "pin_hash": (creds.get("pin_hash") or "").strip(),
-        "app_version": (creds.get("app_version") or "2.0.1070").strip(),
-        "os_version": (creds.get("os_version") or "18.6.2").strip(),
-        "useragent_device": (creds.get("useragent_device") or "Apple iPhone 15 Pro Max, iOS/18.6.2").strip(),
-        "user_agent": (creds.get("user_agent") or "Personal%20Pay/2.0.1070 CFNetwork/3826.600.41 Darwin/24.6.0").strip(),
+        "username": _clean_text(creds.get("username")),
+        "password": _clean_text(creds.get("password")).strip('"').strip("'"),
+        "device_id": device_id,
+        "push_device_token": _clean_text(_first_nonempty_str(
+            creds.get("push_device_token"),
+            creds.get("pushDeviceToken"),
+            push.get("deviceToken"),
+        )),
+        "auth_token": auth_token,
+        "pin_hash": _clean_text(creds.get("pin_hash")),
+        "app_version": _first_nonempty_str(creds.get("app_version"), creds.get("appVersion"), headers.get("appversion"), "2.0.1070"),
+        "os_version": _first_nonempty_str(creds.get("os_version"), creds.get("osVersion"), headers.get("osversion"), "18.6.2"),
+        "useragent_device": _clean_text(_first_nonempty_str(creds.get("useragent_device"), creds.get("useragent"), headers.get("useragent"), "Apple iPhone 15 Pro Max, iOS/18.6.2")),
+        "user_agent": _clean_user_agent(_first_nonempty_str(creds.get("user_agent"), creds.get("userAgent"), headers.get("User-Agent"), headers.get("user-agent"), "Personal%20Pay/2.0.1070 CFNetwork/3826.600.41 Darwin/24.6.0")),
     }
 
 
 def _base_headers(c: dict) -> dict:
     # User-Agent как в приложении — без замены %20 на пробел
-    ua = (c["user_agent"] or "Personal%20Pay/2.0.1070 CFNetwork/3826.600.41 Darwin/24.6.0").strip()
+    ua = _clean_user_agent(c["user_agent"] or "Personal%20Pay/2.0.1070 CFNetwork/3826.600.41 Darwin/24.6.0")
     return {
         "Accept": "application/json, text/plain, */*",
         "Accept-Language": "ru",
         "Content-Type": "application/json",
         "appversion": c["app_version"],
         "osversion": c["os_version"],
         "useragent": c["useragent_device"],
         "User-Agent": ua,
     }
 
 
 def _paygilant_id(device_id: str) -> str:
     return f"{device_id}_{int(time.time() * 1000)}"
 
 
 def _get_token(session: requests.Session, c: dict) -> tuple:
     """Возвращает (значение для заголовка Authorization, paygilant_session_id).
     Personal Pay в перехвате шлёт Authorization без 'Bearer ' — только голый JWT. Так и отдаём."""
     if c.get("auth_token"):
-        token = c["auth_token"].strip()
-        # Убираем Bearer, если пользователь вставил — приложение шлёт только eyJ...
-        if token.upper().startswith("BEARER "):
-            token = token[7:].strip()
+        token = _clean_auth_token(c["auth_token"])
         return token, _paygilant_id(c["device_id"] or "no_device")
     if not all([c.get("device_id"), c.get("username"), c.get("password"), c.get("push_device_token")]):
         raise ValueError("Заполни device_id, username, password, push_device_token или задай auth_token")
     paygilant = _paygilant_id(c["device_id"])
     headers = _base_headers(c) | {"x-fraud-paygilant-session-id": paygilant}
     payload = {
         "deviceId": c["device_id"],
         "username": c["username"],
         "password": c["password"],
         "useCase": "signin",
         "pushNotifications": {"deviceToken": c["push_device_token"]},
     }
     r = session.post(f"{c['base_url']}/authority/v4/login", headers=headers, json=payload, timeout=HTTP_TIMEOUT)
     if r.status_code not in (200, 201):
         raise RuntimeError(f"Login failed: {r.status_code} {r.text[:500]}")
     body = r.json()
     tokens = body.get("tokens") or {}
     token = (
         tokens.get("idToken")
         or tokens.get("id_token")
         or tokens.get("accessToken")
         or tokens.get("access_token")
         or body.get("token")
     )
     if not token:
         h = {k.lower(): v for k, v in (r.headers or {}).items()}
         token = (h.get("authorization") or "").replace("Bearer ", "").strip()
     if not token:
         raise RuntimeError("Токен не найден в ответе логина. Задай PP_AUTH_TOKEN из перехвата.")
     return token, paygilant
 
 
 def get_accounts(credentials: dict) -> dict:
     """GET financial-accounts — для отображения счетов."""
     c = _norm_creds(credentials)
-    session = requests.Session()
+    session = _new_session()
     token, paygilant = _get_token(session, c)
     headers = _base_headers(c) | {
         "Authorization": token,
         "x-fraud-paygilant-session-id": paygilant,
     }
     r = session.get(
         f"{c['base_url']}/payments/accounts-service/v1/financial-accounts",
         headers=headers,
         timeout=HTTP_TIMEOUT,
     )
     r.raise_for_status()
     return r.json()
 
 
 def _first_account_dict(accounts):
     """Из списка счетов вернуть первый элемент-словарь (если элемент — список, взять его первый dict)."""
     if not accounts:
         return {}
     first = accounts[0]
     if isinstance(first, dict):
         return first
     if isinstance(first, list) and first:
         return first[0] if isinstance(first[0], dict) else {}
     return {}
 
@@ -163,124 +259,124 @@ def get_balance(credentials: dict) -> dict:
         acc_obj.get("accountNumber"),
         acc_obj.get("accountId"),
         first.get("id"),
         first.get("number"),
         first.get("accountNumber"),
         first.get("cvu"),
     )
     # Реальный алиас/название счёта из API (например "Disponible")
     alias = _first_nonempty(
         first.get("name"),
         first.get("alias"),
         first.get("description"),
         acc_obj.get("name"),
         acc_obj.get("alias"),
     )
     return {
         "balance": balance,
         "cvu_number": number,
         "cvu_alias": alias,
         "raw_accounts": data,
     }
 
 
 def beneficiary_discovery(credentials: dict, destination: str) -> dict:
     c = _norm_creds(credentials)
-    session = requests.Session()
+    session = _new_session()
     token, paygilant = _get_token(session, c)
     headers = _base_headers(c) | {
         "Authorization": token,
         "x-fraud-paygilant-session-id": paygilant,
     }
     dest = destination.strip()
     r = session.get(
         f"{c['base_url']}/payments/cashout/b2c-bff-service/transfers/beneficiary-discovery/{dest}",
         headers=headers,
         timeout=HTTP_TIMEOUT,
     )
     r.raise_for_status()
     return r.json()
 
 
 def create_withdraw(
     credentials: dict,
     destination: str,
     amount: float,
     comments: str = "Varios (VAR)",
 ) -> dict:
     c = _norm_creds(credentials)
-    session = requests.Session()
+    session = _new_session()
     token, paygilant = _get_token(session, c)
     headers = _base_headers(c) | {
         "Authorization": token,
         "x-fraud-paygilant-session-id": paygilant,
     }
     tx_id = str(uuid.uuid1())
     payload = {
         "amount": float(amount),
         "transactionId": tx_id,
         "comments": comments,
         "destination": destination.strip(),
-        "additionalInfo": {"sessionId": paygilant, "deviceId": "no_device_id"},
+        "additionalInfo": {"sessionId": paygilant, "deviceId": c["device_id"] or "no_device_id"},
     }
     r = session.post(
         f"{c['base_url']}/payments/cashout/b2c-bff-service/transferences/commit-outer",
         headers=headers,
         json=payload,
         timeout=HTTP_TIMEOUT,
     )
     if r.status_code >= 400:
         try:
             body = r.json()
         except Exception:
             body = {"raw": r.text}
         raise RuntimeError(f"{r.status_code} {body}")
     return r.json()
 
 
 def get_activities_list(credentials: dict, offset: int = 0, limit: int = 15) -> dict:
     """История операций: GET mobile.prod.../platform/transactional-activity/v1/activities-list."""
     c = _norm_creds(credentials)
-    session = requests.Session()
+    session = _new_session()
     token, paygilant = _get_token(session, c)
     headers = _base_headers(c) | {
         "Authorization": token,
         "x-fraud-paygilant-session-id": paygilant,
     }
     params = {"page[offset]": offset, "page[limit]": limit}
     r = session.get(
         f"{c['base_url']}/platform/transactional-activity/v1/activities-list",
         headers=headers,
         params=params,
         timeout=HTTP_TIMEOUT,
     )
     r.raise_for_status()
     return r.json()
 
 
 def get_transference_details(credentials: dict, transaction_id: str) -> dict:
     """Детали перевода (чек). Пробуем mobile.prod (как в приложении), затем prod."""
     c = _norm_creds(credentials)
-    session = requests.Session()
+    session = _new_session()
     token, paygilant = _get_token(session, c)
     tid = transaction_id.strip()
     headers = _base_headers(c) | {
         "Authorization": token,
         "x-fraud-paygilant-session-id": paygilant,
         "x-body-version": "2",
     }
     payload = {"transactionId": tid}
     errors = []
     for base, path, name in [
         (c["base_url"], "/payments/core-transactions/transference", "mobile.prod"),
         ("https://prod.personalpay.dev", "/core-transactions-service/transference", "prod"),
     ]:
         url = base.rstrip("/") + path
         try:
             r = session.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
             r.raise_for_status()
             return r.json()
         except Exception as e:
             resp = getattr(e, "response", None)
             err = f"{resp.status_code} {getattr(resp, 'reason', '')}" if resp is not None else str(e)
             errors.append(f"{name}: {err}")
     raise RuntimeError("Не удалось получить чек: " + "; ".join(errors))
