# 注册资料沿革资料约定

## 身份与名称

- `approval`（批准文号）是唯一正式身份主键；药品名称不是身份。同一通用名、不同批准文号的产品（如片剂与注射剂）各自独立，禁止用通用名把公开注册资料拼成一张产品卡。
- 名称一律作为**别名**保存并区分种类：`product_name`（制剂名称）、`generic_name`（通用名）、`trade_name`（商品名）、`legacy_approval`（旧文号）。别名带出处与适用时段。
- 旧文号只能凭正式 `approval_redirect` 文档归并到当前身份，归并后旧文号仍作为可检索别名保留；重定向目标详情缺失时只形成 `provisional` 临时身份并报缺口，不得假装闭合。

## 来源文档类型

采集器按冻结收据识别六类文档，全部以原文字节 sha256 入库：

| doc_type / 特征 | 实体类型 | 说明 |
| --- | --- | --- |
| 目录（`snapshot_at` + `entries`） | `catalog` | 某观察时点的目录快照，只产生快照型点断言 |
| 详情（`revision`） | `registration` | 批准文号详情，按版次不可变 |
| 变更页（`events`） | `change` | 历史变更事件，带生效日 |
| `approval_redirect` | `redirect` | 旧文号 → 当前文号的正式重定向 |
| 附件（`attachment_id`） | `attachment` | 有效附件信封，正文哈希需核验 |
| `inspection_clues` | `clue` | 抽检线索，只提出候选或确认链接 |

## 字段断言与适用时段

每个字段断言（持有人、剂型、规格、状态、制剂名称）都带 `source_sha256`、`source_url` 与适用时段：

- `snapshot`：快照点断言，`valid_from == valid_to`，只在观察时点成立，不向两端延伸。
- `event`：变更事件背书的新区间，`[valid_from, valid_to)`；后一事件闭合前一区间，`valid_to=null` 表示仍开放。
- `event_prior_value`：事件 `from`/`prior_holder` 明确背书的“此前值”，起点未知（`valid_from=null`），闭合到本次生效日；不臆造起始时间。

目录断言与详情断言**永不合并**。目录与详情更新不同步时，按目录快照日期取当时适用的详情侧取值比对：不一致即报 `CATALOG_DETAIL_OUT_OF_SYNC`，双方各自保留，产品卡只采用详情侧取值并把目录值挂在 `catalog_conflicts` 对照。因此不会出现“新持有人 + 旧制剂规格”的跨页混合值。

## 资料缺口代码

| code | 含义 |
| --- | --- |
| `CATALOG_DETAIL_OUT_OF_SYNC` | 目录与详情在同一适用时点取值冲突，来源不同步 |
| `CHANGE_CHAIN_INCOMPLETE` | 详情版次领先于变更页，对应历史变更事件缺失（首登版次不算缺失） |
| `HOLDER_HISTORY_UNVERIFIED` | 无持有人变更事件，当前持有人只能回溯到详情快照，不能倒推完整持有人历史 |
| `ATTACHMENT_MISSING` | 详情引用的有效附件未采集 |
| `ATTACHMENT_HASH_UNVERIFIABLE` | 已采集附件的正文哈希无法核验，不可作证 |
| `REDIRECT_TARGET_MISSING` | 旧文号重定向目标无正式详情，沿革无法闭合 |
| `CLUE_UNCONFIRMED` | 抽检线索正式标识不足，只保留候选链接，不并入产品卡 |

存在任何缺口时 `publishable=false`。

## 抽检线索链接

- 线索带批准文号（含可经正式重定向解析的旧文号）且目标身份存在 → `confirmed`，确定同一产品。
- 只有名称 → `candidate`，给出全部同名候选身份；名称无命中 → `unconfirmed`。
- 候选/未确认线索不并入任何产品卡，只作为带出处的链接和缺口保留。

## 附件证据等级

- 已采集且正文 sha256 核验通过、且 `effective=true` → `usable_as_evidence=true`。
- 详情引用但未采集 → `captured=false` 并报 `ATTACHMENT_MISSING`。
- 已采集但已失效（如注销通知，`effective=false`）→ 保留收据但不可作为现行证据。

## 账本约束

相同 `(collection, entity_type, entity_id, version)` 的实体若收到不同原文，事务整体回滚（见 `conflict.json`）；未闭合的来源不能被解释成最终处置结果。只依据当前详情与已接收变更，不能推断完整持有人历史。
