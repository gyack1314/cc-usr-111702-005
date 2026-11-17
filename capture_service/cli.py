"""只读重放合成的来源交互并展示接收状态、沿革视图与资料缺口。"""
import argparse
import json
from .transport import Replay
from .ledger import connect, receive
from .view import overview
from .history import build_card, build_identities

COLLECTION = 'registration-product'


def _load(manifest):
    replay = Replay(manifest)
    db = connect(':memory:')
    for step in replay.steps:
        request = step['request']
        raw, receipt = replay.request(request['method'], request['url'])
        receive(db, COLLECTION, raw, receipt)
    return db


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)

    inspect_p = sub.add_parser('inspect', help='重放清单并输出接收总览')
    inspect_p.add_argument('manifest')

    history_p = sub.add_parser('history', help='输出身份、沿革、缺口与线索链接')
    history_p.add_argument('manifest')

    card_p = sub.add_parser('card', help='按正式身份输出单张可回溯产品卡')
    card_p.add_argument('manifest')
    card_p.add_argument('approval')
    card_p.add_argument('--as-of', dest='as_of', default=None,
                        help='按该日期选取适用字段（YYYY-MM-DD），默认最新详情时点')

    gaps_p = sub.add_parser('gaps', help='只输出资料缺口清单')
    gaps_p.add_argument('manifest')

    args = p.parse_args()
    db = _load(args.manifest)
    state = overview(db, COLLECTION)

    if args.command == 'inspect':
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif args.command == 'history':
        print(json.dumps(state.get('history', {}), ensure_ascii=False, indent=2))
    elif args.command == 'gaps':
        print(json.dumps(state['history']['gaps'], ensure_ascii=False, indent=2))
    else:
        from .ledger import entities
        # 用带出处的完整行重建身份，避免卡片丢失 provenance。
        identities, _ = build_identities(entities(db, COLLECTION))
        if args.approval not in identities:
            raise SystemExit('未找到正式身份：%s' % args.approval)
        print(json.dumps(
            build_card(identities, args.approval, args.as_of),
            ensure_ascii=False, indent=2))
    db.close()


if __name__ == '__main__':
    main()
