"""解释批准文号目录、详情、变更、重定向、附件与抽检线索。

每条解释结果都不自带出处：出处由 ledger 写入的收据（url 与 body_sha256）承担，
history 再把收据挂回到每个字段断言上，保证字段值可回溯到冻结原文字节。
"""
import json


def decode(raw, receipt):
    d = json.loads(raw.decode('utf-8'))
    kind = d.get('doc_type')
    if kind == 'catalog' or ('snapshot_at' in d and 'entries' in d):
        return [{'type': 'catalog', 'id': 'catalog@' + d['snapshot_at'],
                 'version': d['snapshot_at'], 'payload': d}]
    if kind == 'approval_redirect':
        return [{'type': 'redirect', 'id': d['legacy_approval'],
                 'version': d['revision'], 'payload': d}]
    if kind == 'inspection_clues':
        return [{'type': 'clue', 'id': c['clue_id'], 'version': d['published_at'],
                 'payload': c} for c in d['clues']]
    if 'attachment_id' in d:
        return [{'type': 'attachment', 'id': d['attachment_id'],
                 'version': d['approval'] + ':' + d['revision'], 'payload': d}]
    if 'events' in d:
        return [{'type': 'change', 'id': d['approval'] + '/' + e['version'],
                 'version': e['version'], 'payload': e} for e in d['events']]
    if 'revision' in d:
        return [{'type': 'registration', 'id': d['approval'],
                 'version': d['revision'], 'payload': d}]
    raise ValueError('注册资料非法')


def assess(rows):
    current = [r for r in rows if r['type'] == 'registration']
    events = [r for r in rows if r['type'] == 'change']
    reasons = [] if current and events else ['登记详情或变更事件缺失']
    if any(r['payload']['changed_at'] > max(
            (x['payload'].get('effective', '') for x in events), default='')
           for r in current):
        reasons.append('当前详情之后的变更链未证实')
    return {'publishable': not reasons, 'reasons': reasons,
            'approval_ids': [r['id'] for r in current]}
