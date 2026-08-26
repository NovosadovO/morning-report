"""
httpreq.py — мінімальна заміна `requests` на чистому urllib.

Причина: у рантаймі Railway модуль `requests` іноді відсутній (build його
ставить, а рантайм-інтерпретатор його не бачить). Через це головний звіт
падав на `import requests as _req_send` уже ПІСЛЯ того, як забрав lock —
і жоден щогодинний звіт не доходив.

Підтримує лише те, що реально треба боту:
    post(url, data=..., files=..., json=..., headers=..., timeout=...)
    get(url, params=..., headers=..., timeout=...)
Відповідь має .status_code, .text, .content, .ok, .json().
"""

import io
import json as _json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

__all__ = ["post", "get", "Response", "HTTPError"]


class HTTPError(Exception):
    pass


class Response:
    def __init__(self, status_code, content, headers=None, url=""):
        self.status_code = int(status_code)
        self.content = content or b""
        self.headers = headers or {}
        self.url = url

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    @property
    def text(self):
        try:
            return self.content.decode("utf-8", "replace")
        except Exception:
            return str(self.content)

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise HTTPError(f"HTTP {self.status_code} for {self.url}")

    def __repr__(self):
        return f"<Response [{self.status_code}]>"


def _as_bytes(v):
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    if hasattr(v, "read"):
        d = v.read()
        return d if isinstance(d, bytes) else str(d).encode("utf-8")
    return str(v).encode("utf-8")


def _norm_file(item):
    """
    Приводить значення files[...] до (filename, bytes, content_type).
    Приймає: bytes / file-like / (name, data) / (name, data, ctype).
    """
    filename, data, ctype = "file", b"", None
    if isinstance(item, (tuple, list)):
        if len(item) >= 1:
            filename = item[0] or "file"
        if len(item) >= 2:
            data = item[1]
        if len(item) >= 3:
            ctype = item[2]
    else:
        data = item
        name = getattr(item, "name", None)
        if name:
            filename = os.path.basename(str(name))
    if not ctype:
        ctype = mimetypes.guess_type(str(filename))[0] or "application/octet-stream"
    return str(filename), _as_bytes(data), ctype


def _multipart(data, files):
    boundary = "----httpreq" + uuid.uuid4().hex
    buf = io.BytesIO()
    dash = ("--" + boundary + "\r\n").encode()

    for key, value in (data or {}).items():
        if value is None:
            continue
        buf.write(dash)
        buf.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        buf.write(_as_bytes(value))
        buf.write(b"\r\n")

    for key, item in (files or {}).items():
        filename, payload, ctype = _norm_file(item)
        buf.write(dash)
        buf.write(
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
        )
        buf.write(f"Content-Type: {ctype}\r\n\r\n".encode())
        buf.write(payload)
        buf.write(b"\r\n")

    buf.write(("--" + boundary + "--\r\n").encode())
    return buf.getvalue(), "multipart/form-data; boundary=" + boundary


def _flat_query(params):
    pairs = []
    for k, v in (params or {}).items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for item in v:
                pairs.append((k, str(item)))
        else:
            pairs.append((k, str(v)))
    return urllib.parse.urlencode(pairs)


def _send(method, url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, r.read(), dict(r.headers), url)
    except urllib.error.HTTPError as e:
        try:
            payload = e.read()
        except Exception:
            payload = b""
        return Response(e.code, payload, dict(getattr(e, "headers", {}) or {}), url)


def post(url, data=None, files=None, json=None, headers=None, timeout=30, **_ignored):
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", "morning-report-bot")

    if files:
        body, ctype = _multipart(data, files)
        hdrs["Content-Type"] = ctype
    elif json is not None:
        body = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(data, (bytes, bytearray)):
        body = bytes(data)
    elif isinstance(data, str):
        body = data.encode("utf-8")
    elif data:
        body = _flat_query(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = b""

    return _send("POST", url, body, hdrs, timeout)


def get(url, params=None, headers=None, timeout=30, **_ignored):
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", "morning-report-bot")
    if params:
        q = _flat_query(params)
        if q:
            url = url + ("&" if "?" in url else "?") + q
    return _send("GET", url, None, hdrs, timeout)
