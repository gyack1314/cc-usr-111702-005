# 药品注册资料的企业产品身份沿革采集

当前登记详情与变更事件分别接收。持有人和剂型的当前字段不能在缺少历史事件时倒推完整沿革。

现有项目从冻结来源响应读取资料，由 transport 保证请求与原文字节身份，domain 解释本领域字段，ledger 保存版本和原始收据，view 展示已确认内容与缺口。样例为合成资料，不访问真实监管站或企业系统。

Python 3.11 以上；在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q capture_service tests
    python -m capture_service.cli inspect data/normal.json

Dockerfile 提供 Linux 环境；docker build -t registration-history . 构建，docker run --rm registration-history 运行样例。
