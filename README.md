# 药品注册资料的企业产品身份沿革采集

当前登记详情与变更事件分别接收。持有人和剂型的当前字段不能在缺少历史事件时倒推完整沿革。

项目从冻结来源响应读取资料，由 transport 保证请求与原文字节身份，domain 解释本领域字段，ledger 保存版本和原始收据，history 构建以批准文号为主键的可回溯身份沿革，view 展示已确认内容与缺口。样例为合成资料，不访问真实监管站或企业系统。

Python 3.11 以上；在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q capture_service tests tools
    python -m capture_service.cli inspect data/normal.json
    python -m capture_service.cli history data/history.json
    python -m capture_service.cli card data/history.json P-8 --as-of 2026-01-15
    python -m capture_service.cli gaps data/history.json

`history.json` 是覆盖目录、详情、变更页、附件、旧文号重定向与抽检线索的冻结清单；
`tools/make_fixtures.py` 重新生成其响应体与 sha256 清单（开发工具，不是运行时代码）：

    python tools/make_fixtures.py

## 核心规则

- 正式身份是批准文号，不是药品名称。同通用名、不同文号/剂型/企业的产品各自独立。
- 名称（制剂名、通用名、商品名、旧文号）一律作为带出处与适用时段的别名保存。
- 每个字段断言都带收据 sha256、来源 URL 与适用时段；目录与详情冲突时各自保留并报
  `CATALOG_DETAIL_OUT_OF_SYNC`，不合成“新持有人 + 旧规格”之类的混合值。
- 缺失的历史变更、附件、重定向目标与标识不足的抽检线索统一作为资料缺口报告；
  有缺口时 `publishable=false`。
- 抽检线索只有在取得足够正式标识（批准文号，或可正式重定向的旧文号）时才确认同一产品，
  否则只保留候选链接。

Dockerfile 提供 Linux 环境；docker build -t registration-history . 构建，docker run --rm registration-history 运行样例。

只依据当前详情与已接收变更，不能推断完整持有人历史。
