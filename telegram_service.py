import os
import json
import threading
import fcntl
import time
from collections import deque
from typing import Dict, Any, List, Optional, Deque, Set

import requests

from ai_core import generate_answer
from memory_store import MemoryStore, handle_local_memory_command


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_LOCK_FILE = ".telegram_bot_worker.lock"
LOCK_STALE_SECONDS = 180


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
    """Singleton polling service for Streamlit.

    Streamlit reruns app.py frequently. This class prevents multiple polling
    workers from being created in the same process and also uses a lightweight
    lock file to reduce duplicate bot instances across reloads.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._last_error = ""
        self._last_update = ""
        self._processed = 0
        self._duplicates_skipped = 0
        self._started_at = ""
        self._worker_id = f"{os.getpid()}-{int(time.time())}"
        self._histories: Dict[str, List[Dict[str, str]]] = {}
        self._seen_queue: Deque[int] = deque(maxlen=500)
        self._seen_set: Set[int] = set()
        self._lock_file = DEFAULT_LOCK_FILE
        self._has_file_lock = False
        self._lock_fd = None

    def status(self) -> Dict[str, Any]:
        alive = self._thread is not None and self._thread.is_alive() and self._running
        return {
            "running": alive,
            "last_error": self._last_error,
            "last_update": self._last_update,
            "processed": self._processed,
            "duplicates_skipped": self._duplicates_skipped,
            "started_at": self._started_at,
            "worker_id": self._worker_id if alive else "",
        }

    def start(self, config: Dict[str, Any]) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                return False

            self._lock_file = str(config.get("lock_file") or DEFAULT_LOCK_FILE)
            if not self._acquire_file_lock():
                self._running = False
                return False

            self._stop_event.clear()
            self._last_error = ""
            self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._worker_id = f"{os.getpid()}-{int(time.time())}"
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(config,),
                daemon=True,
                name="adioranye-telegram-bot-singleton",
            )
            self._thread.start()
            self._running = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._running = False
            self._release_file_lock()

    def _acquire_file_lock(self) -> bool:
        """Acquire an OS-level lock. This is stronger than only checking a file.

        Streamlit can rerun the app and, in some deployments, create more than one
        Python process. fcntl.flock prevents multiple pollers inside the same
        container/filesystem from reading the same Telegram updates.
        """
        try:
            lock_path = os.path.abspath(self._lock_file)
            self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self._lock_fd, 0)
            os.write(
                self._lock_fd,
                json.dumps({
                    "worker_id": self._worker_id,
                    "pid": os.getpid(),
                    "started_at": time.time(),
                }).encode("utf-8"),
            )
            self._has_file_lock = True
            return True
        except BlockingIOError:
            self._last_error = (
                "Bot Telegram sudah aktif di proses lain. Instance baru tidak dijalankan "
                "agar jawaban tidak dobel."
            )
            return False
        except Exception as exc:
            self._last_error = f"Gagal membuat lock bot Telegram: {exc}"
            return False

    def _heartbeat_lock(self) -> None:
        # Lock is held by an open file descriptor; no heartbeat needed.
        return

    def _release_file_lock(self) -> None:
        if not self._has_file_lock:
            return
        try:
            if self._lock_fd is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None
        except OSError:
            pass
        self._has_file_lock = False

    def reset_telegram_session(self, config: Dict[str, Any]) -> str:
        """Reset webhook and pending updates for this bot token.

        This cannot kill old deployments that are still running elsewhere, but it
        clears Telegram-side pending updates and helps after a redeploy/reboot.
        """
        token = config.get("telegram_token", "")
        if not token:
            return "TELEGRAM_BOT_TOKEN belum diisi."
        self.stop()
        try:
            self._telegram_post(token, "deleteWebhook", {"drop_pending_updates": True}, timeout=20)
            data = self._telegram_post(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 1}, timeout=10)
            return "Sesi Telegram direset. Pending update dibersihkan. Jika masih double/triple, revoke token di BotFather karena masih ada instance lama di luar app ini."
        except Exception as exc:
            return f"Gagal reset sesi Telegram: {exc}"
        self._lock_fd = None

    def _remember_update(self, update_id: int) -> bool:
        """Return True if update is new, False if duplicate."""
        if update_id in self._seen_set:
            self._duplicates_skipped += 1
            return False
        if len(self._seen_queue) == self._seen_queue.maxlen:
            old = self._seen_queue.popleft()
            self._seen_set.discard(old)
        self._seen_queue.append(update_id)
        self._seen_set.add(update_id)
        return True

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

    def _send_typing(self, token: str, chat_id: int) -> None:
        try:
            self._telegram_post(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except Exception:
            # Typing indicator is optional.
            pass

    def _run_loop(self, config: Dict[str, Any]) -> None:
        token = config.get("telegram_token", "")
        api_key = config.get("slashai_api_key", "")
        api_url = config.get("slashai_api_url", "")
        model = config.get("slashai_model", "slashai/gpt-5-nano")
        persona = config.get("persona", "")
        memory_file = config.get("memory_file", "assistant_memory.json")
        fallback_models = config.get("fallback_models") or []
        drop_pending_updates = bool(config.get("drop_pending_updates", True))
        send_processing_message = bool(config.get("send_processing_message", False))

        if not token:
            self._last_error = "TELEGRAM_BOT_TOKEN belum diisi."
            self._running = False
            self._release_file_lock()
            return

        memory = MemoryStore(memory_file)
        offset = None

        try:
            # drop_pending_updates=True prevents old messages from being answered twice
            # after Streamlit restarts or wakes from sleep.
            self._telegram_post(
                token,
                "deleteWebhook",
                {"drop_pending_updates": drop_pending_updates},
                timeout=20,
            )
        except Exception as exc:
            self._last_error = f"Gagal deleteWebhook: {exc}"

        try:
            while not self._stop_event.is_set():
                self._heartbeat_lock()
                try:
                    payload = {"timeout": 25, "limit": 10, "allowed_updates": ["message"]}
                    if offset is not None:
                        payload["offset"] = offset

                    data = self._telegram_post(token, "getUpdates", payload, timeout=35)
                    updates = data.get("result", [])

                    for update in updates:
                        update_id = int(update.get("update_id", 0))
                        offset = update_id + 1

                        if not self._remember_update(update_id):
                            continue

                        message = update.get("message") or {}
                        chat = message.get("chat") or {}
                        chat_id = chat.get("id")
                        text = (message.get("text") or "").strip()

                        if not chat_id or not text:
                            continue

                        self._last_update = f"Chat {chat_id}: {text[:120]}"
                        self._processed += 1

                        text_lower = text.lower()
                        if text_lower in {"/start", "start"}:
                            self._send_message(
                                token,
                                chat_id,
                                "Halo, saya <b>adioranye</b>. Kirim pertanyaan apa saja, nanti saya bantu jawab.",
                            )
                            continue

                        if text_lower in {"/help", "help"}:
                            self._send_message(
                                token,
                                chat_id,
                                "Perintah:\n"
                                "/start - mulai bot\n"
                                "/help - bantuan\n\n"
                                "Langsung kirim pertanyaan untuk dijawab AI."
                            )
                            continue

                        local_reply = handle_local_memory_command(text, memory)
                        if local_reply:
                            self._send_message(token, chat_id, local_reply)
                            continue

                        key = str(chat_id)
                        history = self._histories.setdefault(key, [])
                        memory_text = memory.as_prompt_text(limit=20)

                        if send_processing_message:
                            self._send_message(token, chat_id, "⏳ Sedang diproses...")
                        else:
                            self._send_typing(token, chat_id)

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
                            self._send_message(
                                token,
                                chat_id,
                                "Maaf, bot belum bisa menjawab.\n\nDetail ringkas:\n" + str(exc)[:1200],
                            )

                    if not updates:
                        time.sleep(0.5)

                except Exception as exc:
                    self._last_error = str(exc)
                    time.sleep(4)
        finally:
            self._running = False
            self._release_file_lock()


_service = TelegramBotService()


def get_telegram_service() -> TelegramBotService:
    return _service
