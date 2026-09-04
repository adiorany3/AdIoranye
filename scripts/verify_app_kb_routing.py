import ast
import sys
from pathlib import Path
from typing import Any, Dict

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from power_features import classify_intent_text

app_path = root / "app.py"
tree = ast.parse(app_path.read_text(encoding="utf-8"))
node = next(
    item
    for item in tree.body
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    and item.name == "_question_profile_from_text"
)
module = ast.Module(body=[node], type_ignores=[])
namespace: dict[str, object] = {
    "Any": Any,
    "Dict": Dict,
    "re": __import__("re"),
    "classify_intent_text": classify_intent_text,
    "detect_auto_live_scraping_need": lambda text: {
        "needed": "hari ini" in str(text).lower(),
    },
}
exec(compile(module, str(app_path), "exec"), namespace)
profile = namespace["_question_profile_from_text"]
assert callable(profile)

casual = profile("Halo, apa kabar?")
assert casual["profile"] == "fast" and casual["kb_needed"] is False

for question in (
    "Lakukan riset tentang kualitas pakan ternak",
    "Analisis dokumen penelitian kesehatan ini",
):
    result = profile(question)
    assert result["profile"] in {"balanced", "deep"}
    assert result["kb_needed"] is True

current = profile("Berapa harga jagung hari ini?")
assert current["profile"] == "current"
assert current["kb_needed"] is False

print("Routing casual, rujukan KB, dan current live-only terverifikasi.")
