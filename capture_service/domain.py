"""解释批准文号详情与沿革事件。"""
import json
def decode(raw,receipt):
    d=json.loads(raw.decode('utf-8'))
    if 'revision' in d:
        return [{'type':'registration','id':d['approval'],'version':d['revision'],
                 'payload':{'product':d['product'],'holder':d['holder'],
                            'dosage':d['dosage'],'changed_at':d['changed_at']}}]
    if 'events' in d:
        return [{'type':'change','id':d['approval']+'/'+e['version'],'version':e['version'],
                 'payload':e} for e in d['events']]
    raise ValueError('注册资料非法')
def assess(rows):
    current=[r for r in rows if r['type']=='registration'];events=[r for r in rows if r['type']=='change']
    reasons=[] if current and events else ['登记详情或变更事件缺失']
    if any(r['payload']['changed_at']>max((x['payload']['effective'] for x in events),default='') for r in current):
        reasons.append('当前详情之后的变更链未证实')
    return {'publishable':not reasons,'reasons':reasons,'approval_ids':[r['id'] for r in current]}
