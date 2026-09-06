"""Tamandata public API transport. Router bodies remain provider-defined."""
import base64
import json
from urllib.parse import urlsplit

import requests

ENDPOINTS = {
    **{path: "GET" for path in (
        "models", "skills", "models/tts", "models/stt", "models/embedding",
        "models/web", "models/info", "audio/voices", "health", "livez", "readyz",
    )},
    **{path: "POST" for path in (
        "chat/completions", "responses", "images/generations", "audio/speech",
        "audio/transcriptions", "embeddings", "search", "web/fetch", "compress",
    )},
}
PUBLIC_ENDPOINTS = {"compress", "health", "livez", "readyz"}
MAX_BYTES = 20_000_000


def api_base(url):
    parsed = urlsplit(str(url).strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL API harus HTTPS tanpa kredensial.")
    if parsed.hostname == "ai.tamandata.com":
        return "https://ai.tamandata.com/v1"
    path = parsed.path.rstrip("/")
    if "/v1" in path:
        path = path.split("/v1", 1)[0] + "/v1"
    elif path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def response_text(payload):
    return "".join(
        part.get("text", "")
        for item in payload.get("output", []) if isinstance(item, dict)
        for part in item.get("content", []) if isinstance(part, dict)
        if part.get("type") == "output_text" and isinstance(part.get("text"), str)
    )


def _sse(response, endpoint):
    """requests buffers packet fragments; blank lines delimit complete SSE events."""
    data, event, text, final, done = [], "", [], {}, False
    size = 0
    for line in response.iter_lines(chunk_size=4096):
        size += len(line or b"")
        if size > MAX_BYTES:
            raise ValueError("Stream terlalu besar.")
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        if line is None:
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            raw = "\n".join(data)
            data = []
            if raw == "[DONE]" and endpoint == "chat/completions":
                done = True
                break
            payload = json.loads(raw)
            kind = payload.get("type", event)
            if payload.get("error") or kind in {"error", "response.failed", "response.incomplete"}:
                raise RuntimeError("Stream provider gagal atau tidak lengkap; jangan retry otomatis.")
            if endpoint == "responses":
                if kind == "response.output_text.delta":
                    text.append(payload.get("delta", ""))
                elif kind == "response.completed":
                    final = payload.get("response", {})
                    done = True
                    break
            else:
                for choice in payload.get("choices", []):
                    if choice.get("finish_reason") == "length":
                        raise RuntimeError("Output terpotong: finish_reason=length.")
                    text.append(choice.get("delta", {}).get("content") or "")
            event = ""
            if sum(map(len, text)) > MAX_BYTES:
                raise ValueError("Stream terlalu besar.")
    if not done:
        raise RuntimeError("Stream terputus sebelum penanda selesai; jangan retry otomatis.")
    return {"text": "".join(text), "response": final, "id": final.get("id")}


def request(api_url, api_key, endpoint, body=None, *, upload=None,
            file_field="file", raw=False, content_type="application/octet-stream", bypass=False):
    """One request, no retries/redirects. upload=(filename, bytes, MIME)."""
    if endpoint not in ENDPOINTS:
        raise ValueError("Endpoint tidak didukung.")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("Body/query harus objek JSON.")
    if len(json.dumps(body).encode()) > 9_000_000:
        raise ValueError("Body JSON terlalu besar.")
    if endpoint == "images/generations":
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Prompt gambar wajib 1–4000 karakter.")
        if set(body) - {"prompt", "model"}:
            raise ValueError("Gambar hanya mendukung prompt dan model opsional.")
    if upload and (endpoint != "audio/transcriptions" or not 0 < len(upload[1]) <= MAX_BYTES):
        raise ValueError("Upload hanya untuk transkripsi, maksimal 20 MB, tidak kosong.")
    if raw and not upload:
        raise ValueError("Mode raw memerlukan file.")
    base = api_base(api_url)
    root = base.rsplit("/v1", 1)[0] if base.endswith("/v1") else base
    url = f"{root if endpoint in PUBLIC_ENDPOINTS - {'compress'} else base}/{endpoint}"
    headers = {}
    if endpoint not in PUBLIC_ENDPOINTS:
        if not str(api_key).strip():
            raise ValueError("API key belum dikonfigurasi.")
        headers["Authorization"] = f"Bearer {api_key}"
    if bypass and endpoint == "compress":
        headers["X-Headroom-Bypass"] = "true"
    options = {"params": body} if ENDPOINTS[endpoint] == "GET" else {"json": body}
    if upload:
        if raw:
            if body:
                raise ValueError("Raw body tidak bisa digabung dengan field JSON.")
            headers["Content-Type"] = content_type
            options = {"data": upload[1]}
        else:
            if not file_field or any(c in file_field for c in '\r\n'):
                raise ValueError("Nama field file tidak valid.")
            options = {"data": body, "files": {file_field: upload}}
    try:
        with requests.request(ENDPOINTS[endpoint], url, headers=headers, timeout=(15, 180),
                              allow_redirects=False, stream=True, **options) as response:
            if not 200 <= response.status_code < 300:
                # Do not echo upstream bodies: they may contain credentials or private inputs.
                raise RuntimeError(f"Tamandata HTTP {response.status_code}; hasil belum pasti, jangan retry otomatis.")
            mime = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].lower()
            if mime == "text/event-stream":
                if endpoint not in {"responses", "chat/completions"}:
                    raise RuntimeError("SSE tidak didukung untuk endpoint ini.")
                return {"json": _sse(response, endpoint), "mime": mime}
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Respons melebihi batas 20 MB.")
                chunks.append(chunk)
            content = b"".join(chunks)
            if mime == "application/json" or mime.endswith("+json"):
                payload = json.loads(content)
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError("Provider mengembalikan error.")
                return {"json": payload, "mime": mime}
            if mime.startswith(("audio/", "image/")) or mime == "application/octet-stream":
                return {"bytes": content, "mime": mime}
            raise RuntimeError("Respons provider bukan JSON/audio yang didukung.")
    except requests.RequestException:
        raise RuntimeError("Koneksi provider gagal; hasil belum pasti, jangan retry otomatis.") from None


def image_outputs(payload):
    """Never fetch provider URLs on server (SSRF); consumers may show/send URL."""
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            yield base64.b64decode(item["b64_json"], validate=True)
        elif isinstance(item.get("url"), str):
            parsed = urlsplit(item["url"])
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                yield item["url"]

def telegram_command(text, *, authorized, api_url, api_key):
    """Private-admin JSON interface; never execute model-supplied tools."""
    if not authorized:
        return "Akses ditolak. Gunakan chat pribadi admin."
    fields = text.split(maxsplit=2)
    if len(fields) < 2:
        return "Format: /tamandata ENDPOINT [OBJEK_JSON]\nEndpoint: " + ", ".join(ENDPOINTS)
    endpoint = fields[1].removeprefix("/v1/").strip("/")
    # Binary input/output requires web upload/download; don't charge for unusable output.
    if endpoint in {"audio/speech", "audio/transcriptions", "images/generations"}:
        return "Gunakan panel Tamandata di /admin web untuk upload, gambar, dan audio."
    try:
        body = json.loads(fields[2]) if len(fields) == 3 else {}
        result = request(api_url, api_key, endpoint, body)
        output = json.dumps(result.get("json", {}), ensure_ascii=False)
        return output[:3000] + ("\n[Dipangkas; gunakan panel web untuk hasil lengkap.]" if len(output) > 3000 else "")
    except (ValueError, RuntimeError):
        return "Permintaan gagal. Periksa endpoint, JSON, akses, dan status provider; jangan retry otomatis."

def render_admin_explorer(api_url, api_key):
    """Called only after app admin authentication; outputs not persisted across sessions."""
    import streamlit as st
    with st.expander("Tamandata — seluruh endpoint API"):
        st.caption("Khusus admin. POST dapat memakai saldo. Schema router mengikuti provider; tidak ada retry otomatis.")
        with st.form("tamandata_request"):
            endpoint = st.selectbox("Endpoint", list(ENDPOINTS))
            raw_body = st.text_area("Body atau query JSON", "{}")
            upload = st.file_uploader("File transkripsi opsional", type=["wav", "mp3", "m4a", "ogg", "webm", "flac"])
            raw = st.checkbox("Kirim audio raw, bukan multipart")
            bypass = st.checkbox("Lewati transformasi compress")
            send = st.form_submit_button("Kirim satu permintaan")
        if send:
            st.session_state.pop("tamandata_result", None)
            try:
                result = request(
                    api_url, api_key, endpoint, json.loads(raw_body),
                    upload=(upload.name, upload.getvalue(), upload.type or "application/octet-stream") if upload else None,
                    raw=raw, content_type=upload.type or "application/octet-stream" if upload else "application/octet-stream",
                    bypass=bypass,
                )
                st.session_state.tamandata_result = (endpoint, result)
            except (ValueError, RuntimeError):
                st.error("Permintaan gagal. Periksa JSON, file, akses, dan status provider; jangan retry otomatis.")
        saved = st.session_state.get("tamandata_result")
        if saved:
            saved_endpoint, result = saved
            if "bytes" in result:
                if result["mime"].startswith("audio/"):
                    st.audio(result["bytes"], format=result["mime"])
                st.download_button("Unduh hasil", result["bytes"], "tamandata-output", mime=result["mime"])
            else:
                payload = result["json"]
                if saved_endpoint == "images/generations" and isinstance(payload, dict):
                    try:
                        for image in image_outputs(payload):
                            if isinstance(image, bytes):
                                st.image(image)
                            else:
                                st.link_button("Buka gambar provider", image)
                    except ValueError:
                        st.warning("Data gambar provider tidak valid.")
                st.json(payload)
                st.download_button("Unduh JSON", json.dumps(payload, ensure_ascii=False), "tamandata.json", "application/json")