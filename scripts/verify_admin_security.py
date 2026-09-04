import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "app.py").read_text(encoding="utf-8")
tree = ast.parse(source)

assignments = {
    node.targets[0].id: node.value
    for node in ast.walk(tree)
    if isinstance(node, ast.Assign)
    and len(node.targets) == 1
    and isinstance(node.targets[0], ast.Name)
}
for name in ("admin_username", "admin_password"):
    rendered = ast.unparse(assignments[name])
    assert "get_secret(" in rendered and "ADMIN_" in rendered, rendered
    assert "'admin'" not in rendered.lower() and '"admin"' not in rendered.lower(), rendered

assert "admin_credentials_configured(admin_username, admin_password)" in source
assert 'failed_attempts >= 5' in source
assert 'time.time() + 60' in source
print("Kredensial default admin ditolak dan login memiliki cooldown.")
