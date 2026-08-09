MAX_PAGE_SIZE = 100

def _clamp_page_size(raw, default=25):
    try:
        v = int(raw)
    except Exception:
        v = default
    return max(1, min(v, MAX_PAGE_SIZE))
