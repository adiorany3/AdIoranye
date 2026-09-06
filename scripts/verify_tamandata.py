"""Offline transport and private-admin boundary checks."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tamandata as api


def main():
    assert len(api.ENDPOINTS) == 20
    assert api.api_base("https://ai.tamandata.com/v1/responses") == "https://ai.tamandata.com/v1"
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.headers = {"Content-Type": "application/json"}
    response.iter_content.return_value = [b'{"data":[]}']
    with patch.object(api.requests, "request", return_value=response) as send:
        for endpoint, method in api.ENDPOINTS.items():
            body = {"prompt": "test"} if endpoint == "images/generations" else {}
            api.request("https://ai.tamandata.com/v1", "fake-test-key", endpoint, body)
            args, kwargs = send.call_args
            assert args[0] == method
            assert ("Authorization" in kwargs["headers"]) == (endpoint not in api.PUBLIC_ENDPOINTS)
            assert kwargs["allow_redirects"] is False
            prefix = "" if endpoint in {"health", "livez", "readyz"} else "/v1"
            assert args[1] == f"https://ai.tamandata.com{prefix}/{endpoint}"
        send.reset_mock()
        assert "ditolak" in api.telegram_command("/tamandata models", authorized=False, api_url="", api_key="")
        assert "panel" in api.telegram_command("/tamandata audio/speech {}", authorized=True, api_url="", api_key="")
        send.assert_not_called()
        api.request("https://ai.tamandata.com", "fake", "audio/transcriptions", upload=("a.wav", b"data", "audio/wav"))
        assert "files" in send.call_args.kwargs
        assert "Content-Type" not in send.call_args.kwargs["headers"]
    response.iter_lines.return_value = [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'', b'data: [DONE]', b'']
    assert api._sse(response, "chat/completions")["text"] == "ok"
    response.iter_lines.return_value = [b'data: {"choices":[]}', b'']
    try:
        api._sse(response, "chat/completions")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Incomplete stream accepted")
    print("PASS: 20 routes, auth, redirects, multipart, SSE completion, admin boundary")


if __name__ == "__main__":
    main()