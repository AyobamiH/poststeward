"""Explain exact comparison failures without treating similar copy as verified."""
import hashlib
import html


def _provider_text(provider, value, entities=None, display_text_range=None):
    """Undo explicit provider presentation layers without relaxing copy equality."""
    if provider != 'x' or not isinstance(value, str):
        return value
    text = value
    if (isinstance(display_text_range, (list, tuple)) and len(display_text_range) == 2
            and all(type(index) is int for index in display_text_range)):
        start, end = display_text_range
        if 0 <= start <= end <= len(text):
            text = text[start:end]
    text = html.unescape(text)
    urls = entities.get('urls', []) if isinstance(entities, dict) else []
    if not isinstance(urls, list):
        return text
    for entity in urls:
        if not isinstance(entity, dict):
            continue
        short = entity.get('url')
        expanded = entity.get('expanded_url')
        if not (isinstance(short, str) and isinstance(expanded, str)):
            continue
        if not short.startswith(('http://', 'https://')) or not expanded.startswith(('http://', 'https://')):
            continue
        if short in text:
            text = text.replace(short, expanded, 1)
    return text


def compare(expected, observed, *, provider=None):
    observed = dict(observed)
    if 'text' in observed:
        observed['text'] = _provider_text(provider, observed.get('text'), observed.get('entities'),
                                          observed.get('display_text_range'))
    matches = {key: observed.get(key) == value for key, value in expected.items()}
    result = {'matches': matches, 'mismatch_fields': [k for k, ok in matches.items() if not ok]}
    if not all(matches.values()):
        # Only explicitly selected public post fields, never HTTP headers/errors.
        result['expected'] = {k: v for k, v in expected.items() if k != 'text'}
        result['observed'] = {k: observed.get(k) for k in expected if k != 'text'}
        for label, values in [('expected', expected), ('observed', observed)]:
            text = values.get('text')
            if isinstance(text, str):
                result[label].update(text=text[:10000], text_length=len(text),
                                     text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                     text_truncated=len(text) > 10000)
    return result
