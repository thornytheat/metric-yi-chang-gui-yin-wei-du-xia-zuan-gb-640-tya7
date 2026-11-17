# anomalyattr · 指标异常归因与维度下钻

酒店集团经营分析组的日指标工具：导入门店 × 渠道 × 日快照，先过质量与对账，再用稳健 z 找出连续异常，最后把净收入差拆成可售房晚、出租率、均价三个因子并按维度下钻。

## 能力

- **采信**：编码回退（UTF-8 / GB18030）、BOM 剥离、重复表头剔除、严格列数、原始行号保留。
- **值归一**：千分位、全角数字、单位后缀、空值哨兵三态、带偏移时间统一到 UTC 再落东八区业务日。
- **质量与隔离**：负值、超售越界、快照时间异常、重复快照冲突各有稳定原因码，隔离行可导出 CSV。
- **异常检测**：工作日 / 休息日分层，滚动 28 个同层点算 median 与 MAD，`|z| ≥ 3.5` 且连续 2 天确认。
- **归因分解**：LMDI 三因子分解，残差显式；维度份额前 5 组单列，其余合并并吸收尾差。
- **批次与清单**：`run_id` 由输入 sha256 + 规则版本 + 参数摘要派生，同源重跑直接复用。
- **异常复盘**：确认事件生成复盘卡（基线、因子、动作、置信），并对窗口与阈值扫九个组合；翻转的结论标 `unstable`。
- **退网清除（M8）**：按门店不可逆清除可定位本店的明细，先落不含门店标识的汇总凭据再删行，旧批次四账按剩余数据复算；报告核对清除前后差额与采信/隔离/复盘残留，中断可续跑、清除优先于导入、同文件重导不恢复退网门店。

## 模块

| 模块 | 入口 | 规则 |
|---|---|---|
| M1 指标接入 | `POST /api/import`、`POST /api/import/preview` | 解码回退链、列映射、三态空输入 |
| M2 日历与归一 | `GET /api/contract`、`POST /api/contract/preview` | 半开日桶、事件时区归一、哨兵三态 |
| M3 质量隔离 | `GET /api/quarantine`、`GET /api/quarantine.csv` | 重复快照归因、迟到标记、三账 |
| M4 异常检测 | `GET /api/anomalies` | 同层窗口 median/MAD、连续确认、MAD 为 0 的退路 |
| M5 归因与批次 | `GET /api/attribution`、`POST /api/attribution/rerun`、`GET /api/batches` | LMDI 三因子、份额守恒、幂等重跑 |
| M6 报告与导出 | `GET /api/report`、`GET /api/export/{kind}` | 四账、清单、证据行导出 |
| M7 异常复盘 | `GET /api/review`、`GET /api/review/{event_id}`、`GET /api/review/sensitivity`、`GET /api/review/cards.csv` | 剔除节假日的基线、LMDI 因子、敏感性九组合、同批异常 |
| M8 退网清除 | `POST /api/purges`、`GET /api/purges`、`GET /api/purges/{purge_id}`、凭据 CSV | 无门店标识凭据承接、四账差额、残留核对、中断续跑、清除优先 |

## 核心规则

净收入 `V = 可售房晚 × 出租率 × 均价`。对每个维度组比较基期与当期：

- 因子贡献 `= L(V当期, V基期) × ln(f当期 / f基期)`，`L(a,b) = (a−b)/(ln a − ln b)`，`a = b` 时取该值。
- `ΔV = Δ可售 + Δ出租率 + Δ均价 + 残差`，残差量化到分后显式输出。
- 份额 = 组差额 ÷ 总差额；「其他」吸收尾差，合计恒为 1.0000。

异常判定：当日之前最近 28 个同层点算 median 与 MAD；`z = 0.6745 × (x − median) / MAD`；`|z| ≥ 3.5` 为候选，相邻业务日同向连续 2 天为确认；同层点少于 14 不判；MAD 为 0 退到平均绝对偏差，仍为 0 则本点不判。

复盘：对每条确认异常重算基线——同层窗口先剔节假日点（占三成以上记 `R_HOLIDAY_THIN`），离散度为 0 时不做除法、记 `R_NO_DISPERSION`；再用窗口 21/28/35 × 阈值 2.5/3.5/4.5 扫九个组合，任一组不再成立即标 `unstable`。

字段定义、时区、去重键、空态与对账等式见 `docs/DATA-DICTIONARY.md`；陷阱与测试映射见 `docs/DEPTH.md`。

## 启动

```bash
docker compose config --quiet
docker compose up -d --build
curl -s http://127.0.0.1:18209/healthz
docker compose exec -T app pytest -q
docker compose down -v --remove-orphans
```

镜像内已带样例数据，启动时若无批次会自动导入主样例（`ANOMALYATTR_SEED=1`），页面首次打开即有数据。

## 演示步骤

1. `/` 看四账与原因码，点「仅采信」查看编码与逐行诊断。
2. `/anomalies` 看水位时间轴与确认点：`H02/ota` 11-17 → 11-18、`H01/member` 11-14 → 11-15；`H03/direct` 11-19 只有候选。
3. `/attribution` 看三因子与残差，`/drilldown` 看渠道与门店份额。
4. `/batches` 点「重跑」，批次号不变、行数不变。
5. `/report` 导出四个文件，逐行带行号与原因码。
6. `/review` 看复盘卡与敏感性矩阵；在 `/` 用选样例导入 `review_marginal.csv` 后回 `/review`，可以看到翻转的组合与同批异常。

## 接口与页面

页面 8 个：`/`、`/anomalies`、`/review`、`/attribution`、`/drilldown`、`/batches`、`/report`、`/purge`；接口清单与失败码见 `docs/API.md`，交互式文档在 `/docs`。

## 目录

```text
app/
  main.py config.py errors.py reasons.py manifest.py store.py web.py
  modules/m1_ingest/ m2_calendar/ m3_quality/ m4_detect/ m5_attrib/ m6_export/ m7_review/ m8_purge/
  templates/  static/
docs/DEPTH.md docs/DATA-DICTIONARY.md docs/API.md docs/SAMPLES.md
tests/ conftest.py test_<模块>.py test_e2e.py fixtures/expected.py
sample_data/ 7 个样例
```
