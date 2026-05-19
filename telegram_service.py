import threading
import time
from typing import Dict, Any, List, Optional

import requests

from ai_core import generate_answer
from memory_store import MemoryStore, handle_local_memory_command


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def split_telegram_message(text: str, max_len: int = 3900) -> List[str]:
    text = text or ""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


class TelegramBotService:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._last_error = ""
        self._last_update = ""
        self._processed = 0
        self._started_at = ""
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def status(self) -> Dict[str, Any]:
        alive = self._thread is not None and self._thread.is_alive() and self._running
        return {
            "running": alive,
            "last_error": self._last_error,
            "last_update": self._last_update,
            "processed": self._processed,
            "started_at": self._started_at,
        }

    def start(self, config: Dict[str, Any]) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                return False

            self._stop_event.clear()
            self._last_error = ""
            self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(config,),
                daemon=True,
                name="adioranye-telegram-bot",
            )
            self._thread.start()
            self._running = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._running = False

    def _telegram_post(self, token: str, method: str, payload: Dict[str, Any], timeout: int = 35) -> Dict[str, Any]:
        url = TELEGRAM_API.format(token=token, method=method)
        resp = requests.post(url, json=payload, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Telegram response bukan JSON: {resp.text[:1000]}")

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error {method}: {data}")
        return data

    def _send_message(self, token: str, chat_id: int, text: str) -> None:
        for chunk in split_telegram_message(text):
            self._telegram_post(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )

    def _run_loop(self, config: Dict[str, Any]) -> None:
        token = config.get("telegram_token", "")
        api_key = config.get("slashai_api_key", "")
        api_url = config.get("slashai_api_url", "")
        model = config.get("slashai_model", "slashai/gpt-5-nano")
        persona = config.get("persona", "")
        memory_file = config.get("memory_file", "assistant_memory.json")
        fallback_models = config.get("fallback_models") or []

        if not token:
            self._last_error = "TELEGRAM_BOT_TOKEN belum diisi."
            self._running = False
            return

        memory = MemoryStore(memory_file)
        offset = None

        try:
            self._telegram_post(token, "deleteWebhook", {"drop_pending_updates": False}, timeout=20)
        except Exception as exc:
            self._last_error = f"Gagal deleteWebhook: {exc}"

        while not self._stop_event.is_set():
            try:
                payload = {"timeout": 25, "limit": 10, "allowed_updates": ["message"]}
                if offset is not None:
                    payload["offset"] = offset

                data = self._telegram_post(token, "getUpdates", payload, timeout=35)
                updates = data.get("result", [])

                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    text = (message.get("text") or "").strip()

                    if not chat_id or not text:
                        continue

                    self._last_update = f"Chat {chat_id}: {text[:120]}"
                    self._processed += 1

                    if text.lower() in {"/start", "start"}:
                        self._send_message(token, chat_id, "Halo, saya <b>adioranye</b>. Kirim pertanyaan apa saja, nanti saya bantu jawab.")
                        continue

                    if text.lower() in {"/help", "help"}:
                        self._send_message(
                            token,
                            chat_id,
                            "Perintah:\n"
                            "/start - mulai bot\n"
                            "/ingat isi memori - simpan memori\n"
                            "/memori - lihat memori\n"
                            "/lupa kata - hapus memori yang mengandung kata\n"
                            "/reset memori - hapus semua memori\n\n"
                            "Selain perintah itu, langsung kirim pertanyaan."
                        )
                        continue

                    local_reply = handle_local_memory_command(text, memory)
                    if local_reply:
                        self._send_message(token, chat_id, local_reply)
                        continue

                    key = str(chat_id)
                    history = self._histories.setdefault(key, [])
                    memory_text = memory.as_prompt_text(limit=20)

                    self._send_message(token, chat_id, "⏳ Sedang diproses...")

                    try:
                        answer, meta = generate_answer(
                            api_url=api_url,
                            api_key=api_key,
                            model=model,
                            system_prompt=persona,
                            user_text=text,
                            memory_text=memory_text,
                            recent_messages=history,
                            fallback_models=fallback_models,
                            temperature=float(config.get("temperature", 0.3)),
                            max_completion_tokens=int(config.get("max_completion_tokens", 1800)),
                            timeout=int(config.get("timeout", 60)),
                        )

                        history.append({"role": "user", "content": text})
                        history.append({"role": "assistant", "content": answer})
                        self._histories[key] = history[-8:]
                        self._send_message(token, chat_id, answer)

                    except Exception as exc:
                        self._last_error = str(exc)
                        self._send_message(token, chat_id, "Maaf, bot belum bisa menjawab.\n\nDetail ringkas:\n" + str(exc)[:1200])

                if not updates:
                    time.sleep(0.5)

            except Exception as exc:
                self._last_error = str(exc)
                time.sleep(4)

        self._running = False


_service = TelegramBotService()


def get_telegram_service() -> TelegramBotService:
    return _service
