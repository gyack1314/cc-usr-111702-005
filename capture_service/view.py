"""按本项目约定展示已接收资料、可回溯沿革与明确缺口。"""
from .ledger import entities
from .domain import assess
from .history import build_overview


def overview(c, collection):
    rows = entities(c, collection)
    result = assess(rows)
    result['collection'] = collection
    result['records'] = [
        {'type': r['type'], 'id': r['id'], 'version': r['version'],
         'source_sha256': r['source'], 'source_url': r['source_url']}
        for r in rows]
    if rows:
        result['history'] = build_overview(rows)
    return result
