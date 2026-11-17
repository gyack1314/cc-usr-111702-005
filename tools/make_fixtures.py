"""生成带字节身份的冻结来源夹具（开发工具，不是运行时采集代码）。

在项目根执行：python tools/make_fixtures.py
重算 data/ 下合成响应的 sha256 并重写 history.json，使 transport 重放可核验。
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
ORIGIN = 'https://registry.example.test'


def write_body(name, document):
    raw = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    (DATA / name).write_bytes(raw)
    return {'file': name, 'status': 200, 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def get(url):
    return {'method': 'GET', 'url': url,
            'body_sha256': hashlib.sha256(b'').hexdigest()}


def main():
    steps = []

    def step(url, name, document):
        response = write_body(name, document)
        steps.append({'request': get(url), 'response': response})

    # 目录快照：观察时点 2026-09-15，对 P-8/P-9 均已滞后于详情页。
    step(f'{ORIGIN}/catalog', 'catalog.bin', {
        'snapshot_at': '2026-09-15',
        'entries': [
            {'approval': 'P-8', 'product': '盐酸莫西沙星片', 'holder': '企业乙',
             'dosage': '片剂', 'spec': '0.5g', 'status': 'valid'},
            {'approval': 'P-9', 'product': '阿莫西林胶囊', 'holder': '企业丁',
             'dosage': '胶囊剂', 'spec': '0.25g', 'status': 'valid'},
            {'approval': 'P-OLD-10', 'product': '布洛芬缓释片', 'holder': '企业己',
             'dosage': '片剂', 'spec': '0.3g', 'status': 'redirected',
             'current_approval': 'P-10'},
        ],
    })

    # P-8 详情（当前版次 v3）：现持有人企业甲、现规格 0.4g。
    step(f'{ORIGIN}/products/P-8', 'detail-p8.bin', {
        'approval': 'P-8', 'revision': 'v3', 'product': '盐酸莫西沙星片',
        'generic_name': '盐酸莫西沙星', 'trade_names': ['拜复乐'],
        'holder': '企业甲', 'dosage': '片剂', 'spec': '0.4g',
        'status': 'valid', 'changed_at': '2026-11-01',
        'attachments': [
            {'id': 'A-P8-v3', 'title': 'P-8 现行注册附件', 'effective': True},
            {'id': 'A-P8-MISSING', 'title': 'P-8 生产场地补充附件', 'effective': True},
        ],
    })
    # P-8 变更链只到 v2，详情已是 v3：v3 对应历史事件缺失。
    step(f'{ORIGIN}/products/P-8/changes', 'changes-p8.bin', {
        'approval': 'P-8',
        'events': [
            {'version': 'v1', 'kind': 'holder_transfer', 'effective': '2025-10-01',
             'prior_holder': '企业乙', 'holder': '企业甲'},
            {'version': 'v2', 'kind': 'spec_change', 'effective': '2026-02-01',
             'field': 'spec', 'from': '0.5g', 'to': '0.4g'},
        ],
    })
    step(f'{ORIGIN}/products/P-8/attachments/A-P8-v3', 'attach-p8-v3.bin', {
        'attachment_id': 'A-P8-v3', 'approval': 'P-8', 'revision': 'v3',
        'title': 'P-8 现行注册附件', 'content_type': 'application/pdf',
        'effective': True, 'content_sha256': hashlib.sha256(b'p8-v3-pdf-body').hexdigest(),
        'content_b64': 'cDgtdjMtcGRmLWJvZHk=',
    })

    # P-9 详情：已注销；目录仍标记 valid（不同步）。
    step(f'{ORIGIN}/products/P-9', 'detail-p9.bin', {
        'approval': 'P-9', 'revision': 'v2', 'product': '阿莫西林胶囊',
        'generic_name': '阿莫西林', 'holder': '企业丁', 'dosage': '胶囊剂',
        'spec': '0.25g', 'status': 'revoked', 'changed_at': '2026-05-20',
        'attachments': [{'id': 'A-P9-v2', 'title': 'P-9 注销通知', 'effective': False}],
    })
    # 只有注销事件 v2，首登 v1 事件缺失，且无任何持有人变更事件。
    step(f'{ORIGIN}/products/P-9/changes', 'changes-p9.bin', {
        'approval': 'P-9',
        'events': [
            {'version': 'v2', 'kind': 'cancellation', 'effective': '2026-05-20',
             'field': 'status', 'from': 'valid', 'to': 'revoked'},
        ],
    })
    step(f'{ORIGIN}/products/P-9/attachments/A-P9-v2', 'attach-p9-v2.bin', {
        'attachment_id': 'A-P9-v2', 'approval': 'P-9', 'revision': 'v2',
        'title': 'P-9 注销通知', 'content_type': 'application/pdf',
        'effective': False, 'content_sha256': hashlib.sha256(b'p9-v2-pdf-body').hexdigest(),
        'content_b64': 'cDktdjItcGRmLWJvZHk=',
    })

    # 旧文号重定向：P-OLD-8 有目标详情；P-OLD-10 的目标 P-10 无详情（缺口）。
    step(f'{ORIGIN}/approvals/P-OLD-8/redirect', 'redirect-p8old.bin', {
        'doc_type': 'approval_redirect', 'legacy_approval': 'P-OLD-8',
        'target_approval': 'P-8', 'effective': '2025-10-01',
        'revision': 'r1', 'reason': '批准文号换证',
    })
    step(f'{ORIGIN}/approvals/P-OLD-10/redirect', 'redirect-p10old.bin', {
        'doc_type': 'approval_redirect', 'legacy_approval': 'P-OLD-10',
        'target_approval': 'P-10', 'effective': '2026-03-01',
        'revision': 'r1', 'reason': '批准文号换证',
    })

    # P-11 与 P-8 通用名相同，但企业、剂型不同：禁止用通用名拼成同一张卡。
    step(f'{ORIGIN}/products/P-11', 'detail-p11.bin', {
        'approval': 'P-11', 'revision': 'v1', 'product': '盐酸莫西沙星氯化钠注射液',
        'generic_name': '盐酸莫西沙星', 'holder': '企业戊', 'dosage': '注射剂',
        'spec': '250ml:0.4g', 'status': 'valid', 'changed_at': '2026-06-10',
        'attachments': [],
    })

    # 抽检线索：一条只有通用名（不可定案），其余靠正式标识确认。
    step(f'{ORIGIN}/inspections/clues', 'clues.bin', {
        'doc_type': 'inspection_clues', 'published_at': '2026-11-10',
        'clues': [
            {'clue_id': 'C-100', 'name': '盐酸莫西沙星', 'dosage': '片剂',
             'manufacturer': '企业甲', 'source_notice': '2026 年第 47 期通报'},
            {'clue_id': 'C-101', 'approval': 'P-8', 'name': '盐酸莫西沙星片',
             'manufacturer': '企业甲', 'source_notice': '2026 年第 47 期通报'},
            {'clue_id': 'C-102', 'approval': 'P-OLD-8', 'name': '盐酸莫西沙星片',
             'source_notice': '2026 年第 45 期通报'},
            {'clue_id': 'C-103', 'approval': 'P-9', 'name': '阿莫西林胶囊',
             'manufacturer': '企业丁', 'source_notice': '2026 年第 46 期通报'},
        ],
    })

    manifest = {'allowed_origins': [ORIGIN], 'steps': steps}
    (DATA / 'history.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'wrote {len(steps)} frozen steps -> {DATA / "history.json"}')


if __name__ == '__main__':
    main()
