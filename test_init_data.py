"""Mini App initData tekshiruvi testi. Ishga tushirish: py test_init_data.py"""
import hashlib
import hmac
import json
import os
import urllib.parse

os.environ.setdefault("BOT_TOKEN", "123:test")
import server  # noqa: E402


def make_init_data(token, fields):
    """Telegram rasmiy algoritmi: hash dekodlangan qiymatlardan, natija URL-kodlangan."""
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signed = dict(fields, hash=hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest())
    return urllib.parse.urlencode(signed)


token = server.core.BOT_TOKEN
fields = {"auth_date": "1700000000", "query_id": "AAH",
          "user": json.dumps({"id": 123, "first_name": "Ali Valiyev"})}

good = make_init_data(token, fields)
assert server.validate_init_data(good) == {"id": 123, "first_name": "Ali Valiyev"}

tampered = good.replace("123", "999", 1)
assert server.validate_init_data(tampered) is None
assert server.validate_init_data(make_init_data("000:boshqa", fields)) is None
assert server.validate_init_data("") is None
assert server.validate_init_data("garbage") is None

print("OK: initData tekshiruvi to'g'ri ishlayapti")
