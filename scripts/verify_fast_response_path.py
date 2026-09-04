import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

app_source = (ROOT / "app.py").read_text(encoding="utf-8")
app_tree = ast.parse(app_source)
quick_check_calls = [
    node
    for node in ast.walk(app_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "trigger_question_quick_check_if_needed"
]
assert not quick_check_calls, "Health check sinkron masih dipanggil dari jalur aplikasi."

power_source = (ROOT / "power_features.py").read_text(encoding="utf-8")
assert "if bool(quality_verifier_enabled) and not should_verify_quality:" in power_source

telegram_source = (ROOT / "telegram_service.py").read_text(encoding="utf-8")
assert 'if telegram_parse_bool(self._config.get("send_processing_message"), default=False):' in telegram_source

print("Jalur cepat bebas health check sinkron, verifier opsional, dan pending Telegram opsional.")
