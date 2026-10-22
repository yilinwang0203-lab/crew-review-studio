---
name: crew-review
description: 开发系统时的小团队审查流程：每个计划任务做完后，用本地 dsh 初审、关键检查点再加 Codex 终审，通过文件交接、自动放行或退回。凡是按实施计划执行开发任务（superpowers:executing-plans / 逐任务实现）且项目里有 crew/crew.json 时使用；写实施计划时也用它标注单审/双审。
---

# crew-review 工作约定

脚本：`python3 <本技能目录>/scripts/crew.py --project <项目> <命令>`
使用说明和示例：`README.md`、`examples/demo-project/`。可把整个仓库放进技能目录，保留 scripts、prompts、schema 的相对位置。

## 设计阶段（draft，2026-10-15 用户定）
设计稿和实施计划**不走审查**，固定为：Codex 写 → Claude 修订 → 看板公示。
1. 需求谈清后写需求文件，交 `codex exec`（只写 docs/）起草设计稿和计划；提示词写明不写代码、限定篇幅、只读指定参考段落。
2. Claude 逐条对照需求修订：先把 Codex 原稿另存为 `<文件>.bak-codex`，再直接改；在设计稿和计划**文首写「## 修订记录」**：改了什么、为什么、用户确认项。
3. 在 crew.json 登记 draft 任务，看板自动公示原稿、修订版、修订记录和需求：
   `"t00": {"review": "draft", "requirements": "<需求文件>", "draft": {"title": "…", "summary": "…", "files": [{"label": "设计稿", "original": "….bak-codex", "revised": "…"}]}}`
4. draft 任务不能 snapshot/review/gate（crew.py 会拒绝）；之后每个代码任务的 requirements.md 都要附上修订记录，审查以修订版为准。

## 写计划时
1. 每个代码任务标 `single`（dsh）或 `double`（dsh + Codex）。大模块完成、最终验收必须 `double`；设计稿和计划走上面的 draft，不审。
2. 每个任务写好必做验证命令（退出码 0 为通过，可加 `expect` 文本）。
3. 同步写进项目的 `crew/crew.json`；每个任务的要求原文放 `crew/<任务>/requirements.md`。
4. scan.include 覆盖源码、配置、依赖清单、测试、必要资源；exclude 必含 `crew/**` 和缓存、日志、`*.bak-*`。

## 每个任务做完
1. 写 `crew/<任务>/handoff.md`，第一行写 `一句话：<大白话说这个任务做了什么，30 字内，不写文件名和代码>`（看板任务卡片显示这句）；必含：任务：、改了哪些文件：、为什么这么改：、怎么验证的：、自己担心的地方：、请重点审：；有未关闭问题时逐个 ID 回复"已改（位置）"或"不改（理由）"。
2. `snapshot <任务>` → `review <任务> --by dsh`（双审再 `--by codex`，两者可同时在后台跑：每个审查员一把锁，2026-10-14 起支持并行）→ 两者都结束后 `gate <任务>`。每轮都要跑完 gate 再开下一轮（忘了也没关系：snapshot 会先自动 gate 上一轮；上一轮还有审查员没审完则拒绝开轮）。
3. 从 snapshot 到 gate 结束，不改被审文件、不改 crew.json、handoff、requirements。
4. 默认只读 `round-NN/<审查员>/attempt-NN/review.md` 了解意见，不读过程日志以省 token；排错时可以看 `meta.json`、`stderr.log`、`final.txt` 和必要的事件记录。

## 结果处理
- gate 退出码 0 放行 → 下一个任务。
- 1 退回/失效 → 按 blocker 修改，更新 handoff，重新 snapshot。我认为某条不该改，就在 handoff 写"不改 + 理由"，由提出者下一轮确认；我不能自己关闭 blocker。
- 2 等待 → 还有审查员没跑完；review 返回"未完成"时可同轮重跑（最多 3 次）。
- v0.2.0 起，格式笔误会先自动规整并记录到 meta.normalized；若仍仅因格式错误而未完成，同一次 review 自动补审一次，计入上述 3 次上限。blocker 缺字段不补，带 blocker 特有字段的 polish / hardening 不作降级规整。
- review 退出码 4 = 外部故障（容量不足/断网/限流/额度用完）：不计入 3 次；容量/断网/限流已在同一次 review 内按 60/120/240 秒自动重试过，额度用完不重试；稍后直接重跑 review。
- 3 需要用户裁决 → 停下，在对话里列出分歧点、双方依据、我的建议，等用户决定。用户决定接受现状 → `decide <任务> --accept --reason …`（未关闭问题记入项目级 crew/accepted-risks.json，之后各任务审查员不得再作为 blocker 提出）；用户决定再给一轮 → `approve --kind extra_round`。
- 项目级 crew/accepted-risks.json（由 decide 写入）每轮 snapshot 时冻结进两位审查员指令：已接受的风险不得再作为 blocker；不手改该文件。
- polish 和加固项（hardening：极端条件才触发的问题）只进 ledger.md，不拦放行、不顺手改；定下一个任务范围时再挑。
- blocker 都标了触发概率（normal_use / plausible_failure），极端条件问题审查员应归为 hardening。

## 轮次上限
- 默认每个任务最多 3 轮（2026-10-14 用户定：3 轮内不用问，超过 3 轮再请用户裁决）；crew.json 可写 max_rounds 覆盖。
- 调度器中途崩溃作废的轮次（INVALID.json kind=scheduler_crash）不计入轮次上限；其他失效照常计数。

## 只在用户明确同意后才做
- `approve <任务> --kind change --reason …`：放宽审查级别、验证、扫描范围，或任务要求原文变更。
- `approve <任务> --kind extra_round --reason …`：达到轮次上限后追加一轮。
- `decide <任务> --accept --reason …`：「需要用户裁决」时用户接受现状（只记录决定，不算审查放行）。
从不自行运行 approve，也不手改 baseline.json、issues.json、round-* 目录。

## 前提
- Python 3.11+；macOS / Linux（需要 POSIX 文件锁和进程组）。
- dsh 和 Codex 命令在 PATH 中，或通过 CREW_DSH_BIN / CREW_CODEX_BIN 指定；Codex 已登录。
- dsh 已配置 headless profile；通过 DEEPSEEK_API_KEY 提供 key。macOS 可选用钥匙串服务 crew-review-deepseek-api（账号 deepseek），服务名可用 CREW_KEYCHAIN_SERVICE 覆盖。
- 网页只在本机 127.0.0.1 监听，展示本地记录，不负责执行审查命令。
