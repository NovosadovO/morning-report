import re as _re

def _sanitize_html(text):
    ALLOWED_TAG = r'</?(?:b|i|u|s|code|pre|a(?:\s+href="[^"]*")?)>'
    ENTITY = r'&(?:amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);'
    placeholders = {}
    counter = [0]
    def save(m):
        key = f'\x00PH{counter[0]}\x00'
        placeholders[key] = m.group()
        counter[0] += 1
        return key
    protected = _re.sub(f'(?:{ALLOWED_TAG}|{ENTITY})', save, text)
    protected = _re.sub(r'&', '&amp;', protected)
    protected = _re.sub(r'<', '&lt;', protected)
    protected = _re.sub(r'>', '&gt;', protected)
    for key, val in placeholders.items():
        protected = protected.replace(key, val)
    return protected

tests = [
    ('<b>ПОГОДА</b>', '<b>ПОГОДА</b>'),
    ('Температура < 20°', 'Температура &lt; 20°'),
    ('Трафік: A > B', 'Трафік: A &gt; B'),
    ('<b>Заголовок</b> і 28° > 20°', '<b>Заголовок</b> і 28° &gt; 20°'),
    ('AT&T', 'AT&amp;T'),
    ('<code>test</code>', '<code>test</code>'),
    ('&amp; вже є entity', '&amp; вже є entity'),
]

all_ok = True
for inp, expected in tests:
    result = _sanitize_html(inp)
    ok = result == expected
    status = 'OK' if ok else 'FAIL'
    print(f'{status}  IN:  {inp!r}')
    if not ok:
        print(f'     EXP: {expected!r}')
        print(f'     GOT: {result!r}')
        all_ok = False

print()
print('ALL OK' if all_ok else 'SOME FAILED')
