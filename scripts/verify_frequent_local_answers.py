import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_core import get_frequent_local_answer

answer, meta = get_frequent_local_answer("  Apa yang bisa kamu kerjakan?! ")
assert answer and meta["model_skipped"] and meta["tokens_saved"]
assert get_frequent_local_answer("apa yang bisa kamu kerjakan dengan data terbaru")[0] == ""
assert get_frequent_local_answer("harga terbaru hari ini")[0] == ""

print("Frequent local answer verification passed.")
