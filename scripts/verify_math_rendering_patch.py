from pathlib import Path
import py_compile

app = Path('app.py')
text = app.read_text(encoding='utf-8')

required = [
    'def normalize_math_markdown(',
    'def render_math_markdown(',
    'def placeholder_math_markdown(',
    'MATH_RENDERING_ENABLED',
    'MATH_NORMALIZE_DELIMITERS_ENABLED',
    'render_math_markdown(msg["content"])',
    'placeholder_math_markdown(existing_placeholder, answer)',
]

missing = [item for item in required if item not in text]
if missing:
    raise SystemExit('Patch belum lengkap. Missing: ' + ', '.join(missing))

py_compile.compile(str(app), doraise=True)
print('OK: math rendering patch terpasang dan app.py valid secara sintaks.')
