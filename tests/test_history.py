"""可回溯登记沿革的端到端规则测试。

夹具 data/history.json 是冻结的合成来源；每条断言都必须能回溯到收据字节。
"""
import hashlib
import json
import unittest
from pathlib import Path

from capture_service.transport import Replay
from capture_service.ledger import connect, receive, entities
from capture_service.view import overview
from capture_service.history import (
    build_overview, build_identities, build_card, applicable)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'data' / 'history.json'
COLLECTION = 'registration-product'


def load_rows():
    replay = Replay(MANIFEST)
    db = connect(':memory:')
    for step in replay.steps:
        request = step['request']
        raw, receipt = replay.request(request['method'], request['url'])
        receive(db, COLLECTION, raw, receipt)
    rows = entities(db, COLLECTION)
    return db, replay, rows


class HistoryRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.replay, cls.rows = load_rows()
        cls.overview = build_overview(cls.rows)
        cls.identities, cls.idx = build_identities(cls.rows)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_every_frozen_step_is_received_with_byte_receipt(self):
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],
            len(self.replay.steps))
        for row in self.rows:
            self.assertTrue(row['source'], '实体必须挂接收据 sha256')
            self.assertTrue(row['source_url'].startswith('https://'))

    def test_same_generic_name_keeps_distinct_identities(self):
        # 盐酸莫西沙星：P-8 片剂/企业甲 与 P-11 注射剂/企业戊 不得合并成一张卡。
        approvals = {i['approval'] for i in self.overview['identities']}
        self.assertIn('P-8', approvals)
        self.assertIn('P-11', approvals)
        p8 = self.identities['P-8']['current']
        p11 = self.identities['P-11']['current']
        self.assertEqual(p8['dosage'], '片剂')
        self.assertEqual(p11['dosage'], '注射剂')
        self.assertNotEqual(p8['holder'], p11['holder'])
        # 名称索引把同一通用名解析到两个身份，而不是一个合并体。
        self.assertEqual(
            sorted(self.overview['clue_links'],
                   key=lambda x: x['clue_id'])[0]['candidate_approvals'],
            ['P-11', 'P-8'])

    def test_field_assertions_carry_provenance_and_intervals(self):
        holder = self.identities['P-8']
        for assertion in holder['field_intervals'] + holder['field_points']:
            self.assertIn('source_sha256', assertion)
            self.assertIn('source_url', assertion)
            if assertion['interval_basis'] != 'event_prior_value':
                self.assertIsNotNone(assertion['valid_from'])
        spec = [a for a in holder['field_intervals'] if a['field'] == 'spec']
        self.assertEqual([a['value'] for a in spec], ['0.5g', '0.4g'])
        # 事件前值起点未知，但适用时段闭合到变更生效日，不臆造起始时间。
        prior = spec[0]
        self.assertIsNone(prior['valid_from'])
        self.assertEqual(prior['valid_to'], '2026-02-01')
        self.assertEqual(prior['interval_basis'], 'event_prior_value')

    def test_catalog_detail_out_of_sync_never_merges_values(self):
        gaps = [g for g in self.overview['gaps']
                if g['code'] == 'CATALOG_DETAIL_OUT_OF_SYNC']
        fields = {(g['approval'], g['field']) for g in gaps}
        self.assertIn(('P-8', 'holder'), fields)   # 目录企业乙 vs 详情企业甲
        self.assertIn(('P-8', 'spec'), fields)     # 目录 0.5g vs 详情 0.4g
        self.assertIn(('P-9', 'status'), fields)   # 目录 valid vs 详情 revoked
        # 卡片只取详情侧取值，目录值仅作为 catalog_conflicts 对照保留。
        card = self.overview['cards']['P-8']
        holder_field = next(f for f in card['fields'] if f['field'] == 'holder')
        self.assertEqual(holder_field['value'], '企业甲')
        self.assertEqual(
            holder_field['catalog_conflicts'][0]['value'], '企业乙')
        spec_field = next(f for f in card['fields'] if f['field'] == 'spec')
        self.assertEqual(spec_field['value'], '0.4g')
        self.assertEqual(spec_field['catalog_conflicts'][0]['value'], '0.5g')

    def test_missing_history_change_is_reported_as_gap(self):
        chain = next(g for g in self.overview['gaps']
                     if g['code'] == 'CHANGE_CHAIN_INCOMPLETE')
        self.assertEqual(chain['approval'], 'P-8')
        self.assertEqual(chain['missing_versions'], ['v3'])
        # 首登版次 v1 不得被误报为缺失（P-11 只有 v1）。
        self.assertFalse(
            any(g['code'] == 'CHANGE_CHAIN_INCOMPLETE'
                and g['approval'] == 'P-11'
                for g in self.overview['gaps']))
        # 没有持有人事件时，持有人历史不可证必须显式报告。
        unverified = {g['approval'] for g in self.overview['gaps']
                      if g['code'] == 'HOLDER_HISTORY_UNVERIFIED'}
        self.assertEqual(unverified, {'P-11', 'P-9'})

    def test_point_in_time_card_does_not_anachronistically_join(self):
        # 2026-01-15：持有人已是企业甲，但规格仍为 0.5g（v2 变更尚未生效）。
        card = build_card(self.identities, 'P-8', '2026-01-15')
        values = {f['field']: f['value'] for f in card['fields']}
        self.assertEqual(values['holder'], '企业甲')
        self.assertEqual(values['spec'], '0.5g')
        # 最新卡片必须反映当前规格，且两个字段都带各自出处。
        latest = build_card(self.identities, 'P-8')
        latest_values = {f['field']: f['value'] for f in latest['fields']}
        self.assertEqual(latest_values['spec'], '0.4g')
        self.assertEqual(latest_values['holder'], '企业甲')

    def test_legacy_approval_redirects_to_former_identity_as_alias(self):
        p8 = self.identities['P-8']
        legacy = next(a for a in p8['aliases'] if a['kind'] == 'legacy_approval')
        self.assertEqual(legacy['alias'], 'P-OLD-8')
        self.assertEqual(legacy['valid_from'], '2025-10-01')
        # 目标详情缺失的重定向只能形成临时身份和缺口，不能假装闭合。
        self.assertTrue(self.identities['P-10']['provisional'])
        redirect_gaps = [g for g in self.overview['gaps']
                         if g['code'] == 'REDIRECT_TARGET_MISSING']
        self.assertTrue(any(g['approval'] == 'P-10' for g in redirect_gaps))
        self.assertNotIn('P-10', self.overview['cards'])

    def test_attachments_distinguish_evidence_missing_and_ineffective(self):
        p8 = {a['attachment_id']: a for a in self.identities['P-8']['attachments']}
        self.assertTrue(p8['A-P8-v3']['hash_verified'])
        self.assertTrue(p8['A-P8-v3']['usable_as_evidence'])
        self.assertFalse(p8['A-P8-MISSING']['captured'])
        self.assertFalse(p8['A-P8-MISSING']['usable_as_evidence'])
        p9 = {a['attachment_id']: a for a in self.identities['P-9']['attachments']}
        self.assertTrue(p9['A-P9-v2']['hash_verified'])
        self.assertFalse(p9['A-P9-v2']['usable_as_evidence'])  # 已失效注销通知
        missing = [g for g in self.overview['gaps']
                   if g['code'] == 'ATTACHMENT_MISSING']
        self.assertEqual([g['attachment_id'] for g in missing], ['A-P8-MISSING'])

    def test_clues_need_formal_identifier_to_confirm(self):
        links = {l['clue_id']: l for l in self.overview['clue_links']}
        self.assertEqual(links['C-100']['decision'], 'candidate')
        self.assertIsNone(links['C-100']['matched_approval'])
        self.assertEqual(set(links['C-100']['candidate_approvals']),
                         {'P-8', 'P-11'})
        self.assertEqual(links['C-101']['decision'], 'confirmed')
        self.assertEqual(links['C-101']['matched_approval'], 'P-8')
        # 旧文号线索经正式重定向确认到当前身份。
        self.assertEqual(links['C-102']['decision'], 'confirmed')
        self.assertEqual(links['C-102']['matched_approval'], 'P-8')
        self.assertIn('重定向', links['C-102']['matched_on'])
        self.assertEqual(links['C-103']['matched_approval'], 'P-9')
        # 未确认线索进入缺口，而不是并入任何产品卡。
        clue_gaps = {g['clue_id'] for g in self.overview['gaps']
                     if g['code'] == 'CLUE_UNCONFIRMED'}
        self.assertEqual(clue_gaps, {'C-100'})
        self.assertEqual(self.overview['confirmed_clue_ids'],
                         ['C-101', 'C-102', 'C-103'])

    def test_aliases_keep_names_separate_from_formal_identity(self):
        p8 = self.identities['P-8']
        kinds = {a['kind']: a['alias'] for a in p8['aliases']}
        self.assertEqual(kinds['generic_name'], '盐酸莫西沙星')
        self.assertEqual(kinds['product_name'], '盐酸莫西沙星片')
        self.assertEqual(kinds['trade_name'], '拜复乐')
        self.assertEqual(kinds['legacy_approval'], 'P-OLD-8')
        for alias in p8['aliases']:
            self.assertTrue(alias['source_sha256'])
            self.assertTrue(alias['source_url'])

    def test_overview_is_not_publishable_while_gaps_exist(self):
        self.assertFalse(self.overview['publishable'])
        self.assertTrue(self.overview['gaps'])

    def test_view_overview_keeps_legacy_contract(self):
        view = overview(self.db, COLLECTION)
        self.assertIn('publishable', view)
        self.assertIn('reasons', view)
        self.assertIn('records', view)
        self.assertIn('history', view)
        self.assertTrue(all('source_url' in r for r in view['records']))


class SameVersionConflict(unittest.TestCase):
    def test_conflicting_revision_still_rejected_and_rolled_back(self):
        replay = Replay(ROOT / 'data' / 'conflict.json')
        db = connect(':memory:')
        first = replay.steps[0]['request']
        raw, receipt = replay.request(first['method'], first['url'])
        receive(db, COLLECTION, raw, receipt)
        before = overview(db, COLLECTION)
        nxt = replay.steps[1]['request']
        other, other_receipt = replay.request(nxt['method'], nxt['url'])
        with self.assertRaises(ValueError):
            receive(db, COLLECTION, other, other_receipt)
        self.assertEqual(overview(db, COLLECTION), before)
        self.assertEqual(
            db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
        db.close()


class FixtureManifest(unittest.TestCase):
    def test_manifest_hashes_match_response_bytes(self):
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        for step in manifest['steps']:
            body = (ROOT / 'data' / step['response']['file']).read_bytes()
            self.assertEqual(len(body), step['response']['bytes'])
            self.assertEqual(hashlib.sha256(body).hexdigest(),
                             step['response']['sha256'])
            self.assertEqual(hashlib.sha256(b'').hexdigest(),
                             step['request']['body_sha256'])


if __name__ == '__main__':
    unittest.main()
