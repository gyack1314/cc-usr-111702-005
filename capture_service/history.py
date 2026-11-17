"""构建可回溯的登记沿革。

身份主键是正式批准文号，不是药品名称。同一通用名、不同批准文号的产品各自独立；
旧文号通过正式重定向文档归并到当前身份，且只作为别名与出处保留。
所有字段断言都带 source（收据 sha256）、source_url 与适用时段，跨页拼接被禁止：
目录与详情冲突时各自保留并报不同步，不产生“新持有人 + 旧规格”的混合值。
"""

import base64
import hashlib

TRACKED_FIELDS = ('holder', 'dosage', 'spec', 'status')
# 详情与变更页用词不完全一致时，先归一到受控状态词，避免把同义词误判为冲突。
STATUS_WORDS = {'有效': 'valid', '注销': 'revoked', '已注销': 'revoked',
                '有效状态': 'valid', 'valid': 'valid', 'revoked': 'revoked',
                'cancelled': 'revoked', 'canceled': 'revoked'}


def norm(value, field=None):
    if value is None:
        return None
    text = str(value).strip()
    if field == 'status':
        return STATUS_WORDS.get(text.lower(), STATUS_WORDS.get(text, text))
    return text


def version_order(version):
    """版次排序：抽出尾部整数（v12 -> 12），无法解析时退回字符串比较。"""
    digits = ''.join(ch for ch in str(version) if ch.isdigit())
    return (0, int(digits)) if digits else (1, str(version))


def applicable(holder, field, as_of):
    """返回 as_of 当日适用的详情侧断言：优先覆盖该日的区间，否则取最近快照点。

    valid_from 为 None 表示事件背书的“此前值”，起点未知，区间向左开放。
    """
    def covers(a):
        start_ok = a['valid_from'] is None or a['valid_from'] <= as_of
        end_ok = a['valid_to'] is None or as_of < a['valid_to']
        return start_ok and end_ok

    interval = next((a for a in holder['field_intervals']
                     if a['field'] == field and covers(a)), None)
    if interval:
        return interval
    points = [a for a in holder['field_points']
              if a['field'] == field and a['valid_from'] <= as_of]
    return max(points, key=lambda a: a['valid_from'], default=None)


def _provenance(row):
    return {'source_sha256': row['source'], 'source_url': row['source_url']}


def _point(row, field, value, observed_at):
    """快照型断言：只在观察时点成立，不向两端延伸。"""
    if value is None:
        return None
    out = {'field': field, 'value': norm(value, field),
           'valid_from': observed_at, 'valid_to': observed_at,
           'interval_basis': 'snapshot'}
    out.update(_provenance(row))
    return out


def _interval(row, field, value, valid_from, valid_to=None, basis='event'):
    """事件型断言：适用时段 [valid_from, valid_to)，valid_to 为 None 表示仍开放。"""
    out = {'field': field, 'value': norm(value, field),
           'valid_from': valid_from, 'valid_to': valid_to,
           'interval_basis': basis}
    out.update(_provenance(row))
    return out


def _event_changes(row):
    """把任意 kind 的变更事件归一为 {field: (from,to)}，未知 kind 不臆造字段。"""
    p = row['payload']
    changes = dict(p.get('changes') or {})
    kind = p.get('kind')
    if kind == 'holder_transfer':
        changes.setdefault('holder', (p.get('prior_holder'), p.get('holder')))
    elif kind == 'spec_change':
        changes.setdefault('spec', (p.get('from'), p.get('to')))
    elif kind in ('cancellation', 'status_change'):
        changes.setdefault('status', (p.get('from', 'valid'),
                                      p.get('to', 'revoked')))
    return {f: (a, b) for f, (a, b) in changes.items() if b is not None}


def _index(rows):
    idx = {'registration': {}, 'change': {}, 'catalog': [],
           'attachment': {}, 'redirect': {}, 'clue': []}
    for row in rows:
        t = row['type']
        if t == 'registration':
            idx['registration'].setdefault(row['id'], []).append(row)
        elif t == 'change':
            approval = row['id'].split('/', 1)[0]
            idx['change'].setdefault(approval, []).append(row)
        elif t == 'catalog':
            idx['catalog'].append(row)
        elif t == 'attachment':
            idx['attachment'].setdefault(row['payload']['approval'], []).append(row)
        elif t == 'redirect':
            idx['redirect'][row['id']] = row
        elif t == 'clue':
            idx['clue'].append(row)
    return idx


def _timeline(rows_for_approval, change_rows):
    """合并详情点断言与变更区间断言，并按时序闭合前一区间。"""
    points, intervals = [], []
    for row in sorted(rows_for_approval, key=lambda r: r['payload']['changed_at']):
        p = row['payload']
        observed = p['changed_at']
        for field in TRACKED_FIELDS:
            assertion = _point(row, field, p.get(field), observed)
            if assertion:
                points.append(assertion)
        if p.get('product'):
            points.append(_point(row, 'product', p['product'], observed))
    for row in sorted(change_rows, key=lambda r: (r['payload'].get('effective', ''),
                                                  r['version'])):
        effective = row['payload'].get('effective')
        for field, (from_value, to_value) in _event_changes(row).items():
            for earlier in reversed(intervals):
                if earlier['field'] == field and earlier['valid_to'] is None:
                    earlier['valid_to'] = effective
                    break
            # 事件明确背书的“此前值”：起点未知，只闭合到本次生效日，不臆造起始时间。
            if from_value is not None and not any(
                    a['field'] == field and a['valid_to'] == effective
                    for a in intervals):
                intervals.append(_interval(row, field, from_value, None, effective,
                                           basis='event_prior_value'))
            intervals.append(_interval(row, field, to_value, effective))
    intervals.sort(key=lambda a: (a['field'], a['valid_from'] or ''))
    return points, intervals


def _name_aliases(approval, rows_for_approval, catalog_entries, redirect_in):
    aliases = {}

    def add(name, kind, valid_from, valid_to=None, row=None):
        if not name:
            return
        key = (kind, norm(name))
        entry = aliases.setdefault(key, {
            'alias': norm(name), 'kind': kind,
            'valid_from': valid_from, 'valid_to': valid_to,
            **(_provenance(row) if row else {})})
        if row and not entry.get('source_url'):
            entry.update(_provenance(row))

    for row in rows_for_approval:
        p = row['payload']
        observed = p['changed_at']
        add(p.get('product'), 'product_name', observed, observed, row)
        add(p.get('generic_name'), 'generic_name', observed, observed, row)
        for trade in p.get('trade_names') or []:
            add(trade, 'trade_name', observed, observed, row)
    for entry_row, entry in catalog_entries:
        add(entry.get('product'), 'product_name',
            entry_row['payload']['snapshot_at'], entry_row['payload']['snapshot_at'],
            entry_row)
    for row in redirect_in:
        p = row['payload']
        add(p['legacy_approval'], 'legacy_approval',
            p['effective'], None, row)
    return sorted(aliases.values(), key=lambda a: (a['kind'], a['alias']))


def _attachment_view(approval, detail_rows, attachment_rows):
    referenced = []
    for detail in detail_rows:
        for ref in detail['payload'].get('attachments') or []:
            referenced.append((detail, ref))
    views, missing_ids = [], set()
    for detail, ref in referenced:
        match = next((r for r in attachment_rows
                      if r['payload']['attachment_id'] == ref['id']), None)
        if match is None:
            missing_ids.add(ref['id'])
            views.append({'attachment_id': ref['id'], 'title': ref.get('title'),
                          'captured': False, 'usable_as_evidence': False,
                          'hash_verified': False, 'effective': bool(ref.get('effective')),
                          'source_sha256': detail['source'],
                          'source_url': detail['source_url']})
            continue
        p = match['payload']
        actual = None
        if p.get('content_b64'):
            actual = hashlib.sha256(
                base64.b64decode(p['content_b64'])).hexdigest()
        hash_ok = actual is not None and actual == p.get('content_sha256')
        views.append({'attachment_id': p['attachment_id'], 'title': p.get('title'),
                      'captured': True, 'usable_as_evidence': bool(
                          p.get('effective')) and hash_ok,
                      'hash_verified': hash_ok, 'effective': bool(p.get('effective')),
                      'content_sha256': p.get('content_sha256'),
                      'source_sha256': match['source'],
                      'source_url': match['source_url']})
    return views, missing_ids


def _catalog_lookups(approval, catalog_rows):
    found = []
    for row in catalog_rows:
        for entry in row['payload']['entries']:
            if entry.get('approval') == approval:
                found.append((row, entry))
    return found


def build_identities(rows):
    idx = _index(rows)
    identities = {}

    def identity(approval):
        return identities.setdefault(approval, {
            'approval': approval, 'aliases': [], 'field_points': [],
            'field_intervals': [], 'catalog_assertions': [],
            'attachments': [], 'events': [], 'detail_revisions': [],
            'provisional': False, 'gaps': [],
            '_redirects_in': [], '_detail_rows': [], '_change_rows': [],
            '_catalog_lookups': [], '_missing_attachments': set()})

    # 重定向先落位：有目标详情则归并为旧文号别名；目标缺失则保留临时身份并报缺口。
    for legacy, row in idx['redirect'].items():
        target = row['payload']['target_approval']
        if target in idx['registration']:
            identity(target).setdefault('_redirects_in', []).append(row)
        else:
            target_holder = identity(target)
            target_holder['provisional'] = True
            target_holder['gaps'].append({
                'code': 'REDIRECT_TARGET_MISSING',
                'approval': target,
                'detail': f'旧文号 {legacy} 重定向到 {target}，但目标详情未采集',
                'source_sha256': row['source'], 'source_url': row['source_url']})
            identity(legacy)['gaps'].append({
                'code': 'REDIRECT_TARGET_MISSING', 'approval': target,
                'detail': f'重定向目标 {target} 无正式详情，旧文号无法闭合',
                'source_sha256': row['source'], 'source_url': row['source_url']})

    for approval, detail_rows in idx['registration'].items():
        holder = identity(approval)
        change_rows = idx['change'].get(approval, [])
        points, intervals = _timeline(detail_rows, change_rows)
        holder['field_points'] = points
        holder['field_intervals'] = intervals
        holder['events'] = [{'version': r['version'], **_event_brief(r)}
                            for r in sorted(change_rows, key=lambda r: r['version'])]
        holder['detail_revisions'] = sorted(
            (r['version'] for r in detail_rows))
        latest = max(detail_rows, key=lambda r: r['payload']['changed_at'])
        holder['latest_revision'] = latest['version']
        holder['current'] = {f: norm(latest['payload'].get(f), f)
                             for f in TRACKED_FIELDS}
        holder['current']['product'] = latest['payload'].get('product')
        holder['aliases'] = _name_aliases(
            approval, detail_rows,
            _catalog_lookups(approval, idx['catalog']),
            holder['_redirects_in'])
        views, missing = _attachment_view(
            approval, detail_rows, idx['attachment'].get(approval, []))
        holder['attachments'] = views
        holder['_missing_attachments'] = missing
        holder['_detail_rows'] = detail_rows
        holder['_change_rows'] = change_rows
        holder['_catalog_lookups'] = _catalog_lookups(approval, idx['catalog'])

    # 目录条目对所有字段生成独立点断言，绝不与详情断言合并。
    for catalog_row in idx['catalog']:
        snapshot_at = catalog_row['payload']['snapshot_at']
        for entry in catalog_row['payload']['entries']:
            approval = entry.get('approval')
            holder = identities.get(approval) or identity(approval)
            for field in TRACKED_FIELDS:
                assertion = _point(catalog_row, field, entry.get(field), snapshot_at)
                if assertion:
                    holder['catalog_assertions'].append(assertion)
            holder['aliases'] = _name_aliases(
                approval, holder['_detail_rows'],
                _catalog_lookups(approval, idx['catalog']),
                holder['_redirects_in'])
    return identities, idx


def _event_brief(row):
    p = row['payload']
    return {'kind': p.get('kind'), 'effective': p.get('effective'),
            'changes': _event_changes(row), 'source_sha256': row['source'],
            'source_url': row['source_url']}


def detect_gaps(identities, idx):
    gaps = []
    for approval, holder in sorted(identities.items()):
        if holder.get('provisional'):
            gaps.extend(holder['gaps'])
            continue
        if not holder['_detail_rows']:
            # 无详情的旧文号壳：其缺口已挂在重定向目标上，此处不重复报。
            continue
        revisions = set(holder['detail_revisions'])
        # 首登版次没有历史事件是正常的；只有详情版次领先于变更链才是缺口。
        event_versions = {r['version'] for r in holder['_change_rows']}
        if event_versions:
            latest_event = max(event_versions, key=version_order)
            missing_versions = sorted(
                (v for v in revisions
                 if v not in event_versions
                 and version_order(v) > version_order(latest_event)),
                key=version_order)
        else:
            # 无任何事件时不存在“领先于变更链”的版次；
            # 历史不可证由 HOLDER_HISTORY_UNVERIFIED 另行报告。
            missing_versions = []
        if missing_versions:
            latest = max(holder['_detail_rows'],
                         key=lambda r: r['payload']['changed_at'])
            gaps.append({'code': 'CHANGE_CHAIN_INCOMPLETE', 'approval': approval,
                         'detail': '详情版次 %s 缺少对应变更事件（缺 %s）'
                                   % (','.join(sorted(revisions)),
                                      ','.join(missing_versions)),
                         'missing_versions': missing_versions,
                         'source_sha256': latest['source'],
                         'source_url': latest['source_url']})
        if not any(f == 'holder' for r in holder['_change_rows']
                   for f in _event_changes(r)):
            latest = max(holder['_detail_rows'],
                         key=lambda r: r['payload']['changed_at'])
            gaps.append({'code': 'HOLDER_HISTORY_UNVERIFIED', 'approval': approval,
                         'detail': '无持有人变更事件，当前持有人只能回溯到详情快照，'
                                   '不能倒推完整持有人历史',
                         'source_sha256': latest['source'],
                         'source_url': latest['source_url']})
        for attachment_id in sorted(holder['_missing_attachments']):
            gaps.append({'code': 'ATTACHMENT_MISSING', 'approval': approval,
                         'detail': f'详情引用的有效附件 {attachment_id} 未采集',
                         'attachment_id': attachment_id})
        for view in holder['attachments']:
            if view['captured'] and not view['hash_verified']:
                gaps.append({'code': 'ATTACHMENT_HASH_UNVERIFIABLE',
                             'approval': approval,
                             'detail': '附件 %s 内容哈希无法核验'
                                       % view['attachment_id'],
                             'attachment_id': view['attachment_id'],
                             'source_url': view['source_url']})
        # 目录与详情更新不同步：按目录快照日期取当时适用的详情侧取值比对，
        # 冲突双方各自保留，绝不合成“新持有人 + 旧规格”之类的混合值。
        for assertion in holder['catalog_assertions']:
            field = assertion['field']
            expected = applicable(holder, field, assertion['valid_from'])
            if expected and expected['value'] != assertion['value']:
                gaps.append({'code': 'CATALOG_DETAIL_OUT_OF_SYNC',
                             'approval': approval,
                             'detail': '目录 %s 在 %s 记录 %s=%r，与详情侧适用值 %r '
                                       '不一致；两者各自保留，不合并取值'
                                       % (assertion['source_url'], assertion['valid_from'],
                                          field, assertion['value'], expected['value']),
                             'field': field, 'catalog_value': assertion['value'],
                             'detail_value': expected['value'],
                             'observed_at': assertion['valid_from'],
                             'catalog_source': assertion['source_url'],
                             'detail_source': expected['source_url']})
    return gaps


def _global_name_index(identities):
    index = {}
    for approval, holder in identities.items():
        for alias in holder['aliases']:
            index.setdefault(norm(alias['alias']), set()).add(approval)
        if holder.get('current', {}).get('product'):
            index.setdefault(norm(holder['current']['product']), set()).add(approval)
    return index


def link_clues(identities, idx):
    name_index = _global_name_index(identities)
    legacy_to_target = {legacy: row['payload']['target_approval']
                        for legacy, row in idx['redirect'].items()}
    results = []
    for row in idx['clue']:
        p = row['payload']
        basis = {'source_sha256': row['source'], 'source_url': row['source_url']}
        approval = p.get('approval')
        if approval:
            target = legacy_to_target.get(approval, approval)
            if target in identities and not identities[target].get('provisional'):
                results.append({'clue_id': row['id'], 'decision': 'confirmed',
                                'matched_approval': target,
                                'matched_on': 'approval'
                                + ('（旧文号重定向）' if approval in legacy_to_target else ''),
                                'candidate_approvals': [target], **basis})
            else:
                results.append({'clue_id': row['id'], 'decision': 'unconfirmed',
                                'matched_approval': None,
                                'matched_on': 'approval',
                                'candidate_approvals': [],
                                'detail': f'线索文号 {approval} 无对应正式详情', **basis})
            continue
        name = norm(p.get('name'))
        candidates = sorted(name_index.get(name, set()))
        if candidates:
            results.append({'clue_id': row['id'], 'decision': 'candidate',
                            'matched_approval': None,
                            'matched_on': 'name_only',
                            'candidate_approvals': candidates,
                            'detail': '仅名称命中 %d 个身份，缺少批准文号等正式标识，'
                                      '只能保留候选链接，不能定案' % len(candidates),
                            **basis})
        else:
            results.append({'clue_id': row['id'], 'decision': 'unconfirmed',
                            'matched_approval': None, 'matched_on': 'name',
                            'candidate_approvals': [],
                            'detail': '名称无任何正式身份命中，正式标识不足', **basis})
    confirmed = {r['clue_id'] for r in results if r['decision'] == 'confirmed'}
    gaps = [{'code': 'CLUE_UNCONFIRMED', 'approval': r.get('matched_approval'),
             'detail': '抽检线索 %s 未取得足够正式标识，仅保留候选链接，不并入产品卡'
                       % r['clue_id'],
             'clue_id': r['clue_id'],
             'candidate_approvals': r['candidate_approvals'],
             'source_url': r['source_url'], 'source_sha256': r['source_sha256']}
            for r in results if r['decision'] != 'confirmed']
    return results, gaps, confirmed


def build_card(identities, approval, as_of=None):
    """产品卡只取详情断言；目录断言仅作对照，历史值按适用时段选取。"""
    holder = identities[approval]
    if holder.get('provisional') or not holder['_detail_rows']:
        return {'approval': approval, 'provisional': True,
                'gaps': holder['gaps'], 'fields': [], 'aliases': holder['aliases']}
    as_of = as_of or max(r['payload']['changed_at']
                         for r in holder['_detail_rows'])

    def value_at(field):
        chosen = applicable(holder, field, as_of)
        if chosen is None:
            return None
        catalog_conflicts = [a for a in holder['catalog_assertions']
                             if a['field'] == field
                             and a['valid_from'] <= as_of
                             and a['value'] != chosen['value']]
        return {'field': field, 'value': chosen['value'],
                'valid_from': chosen['valid_from'], 'valid_to': chosen['valid_to'],
                'interval_basis': chosen['interval_basis'],
                'source_sha256': chosen['source_sha256'],
                'source_url': chosen['source_url'],
                'catalog_conflicts': catalog_conflicts}

    fields = [v for f in TRACKED_FIELDS if (v := value_at(f))]
    product = value_at('product')
    return {
        'approval': approval, 'as_of': as_of, 'provisional': False,
        'identity_status': holder['current'].get('status'),
        'product': product, 'fields': fields,
        'aliases': holder['aliases'],
        'effective_attachments': [a for a in holder['attachments']
                                  if a['usable_as_evidence']],
        'field_history': {f: [a for a in holder['field_intervals']
                              if a['field'] == f] for f in TRACKED_FIELDS},
    }


def build_overview(rows):
    identities, idx = build_identities(rows)
    gaps = detect_gaps(identities, idx)
    links, clue_gaps, confirmed_clues = link_clues(identities, idx)
    gaps.extend(clue_gaps)
    cards = {approval: build_card(identities, approval)
             for approval in sorted(identities)
             if not identities[approval].get('provisional')
             and identities[approval]['_detail_rows']}
    return {
        'identities': [_public_identity(identities[a])
                       for a in sorted(identities)],
        'cards': cards,
        'gaps': gaps,
        'clue_links': links,
        'confirmed_clue_ids': sorted(confirmed_clues),
        'publishable': not gaps,
    }


def _public_identity(holder):
    return {k: v for k, v in holder.items() if not k.startswith('_')}
