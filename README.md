# Crew Review Studio（CS）

本地 AI 代码审查工作流与深色看板。实施者写交接包，DSH 初审，关键任务加 Codex 复审；Python 脚本保存证据、核对文件指纹并决定是否放行。

**[在线交互演示](https://yilinwang0203-lab.github.io/crew-review-studio/)** · [MIT 开源许可证](LICENSE) · [参与贡献](CONTRIBUTING.md)

在线版只用模拟数据，可体验看板、独立双审、证据核对、放行和报告下载，不调用模型、不读取或上传访客代码。本期演示开放至 **2026-11-21 11:37（北京时间）**，开源代码与 MIT 授权不随演示到期而终止。

看板只读展示记录，不执行审查、不代替用户批准。仓库不含作者的真实项目、密钥、会话、审查日志和历史备份。

## 环境

- Python 3.11+，macOS 或 Linux；依赖 POSIX 文件锁和进程组，暂不支持原生 Windows。
- 调度器仅使用 Python 标准库。看板的 Word 报告导出依赖 `python-docx`；自动化测试依赖 `pytest`。
- 真实审查需安装并配置 DSH、Codex CLI；模型调用使用各自账号，可能产生费用。
- 本次发布在 macOS 验证；Linux 尚未实测。

## 先看界面

```sh
git clone https://github.com/yilinwang0203-lab/crew-review-studio.git
cd crew-review-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python studio/serve.py
```

打开 <http://127.0.0.1:8765/>。默认显示仓库自带的小项目，也可切换到交互演示；无需模型账号即可查看。服务器仅供本机使用，请保留回环监听地址。

连接自己的项目：

```sh
python studio/serve.py --project /absolute/path/to/project
```

或将 `studio/projects.example.json` 复制为 `studio/projects.json`，填写项目路径。配置文件里的相对路径以配置文件所在目录为基准；命令行路径以当前工作目录为基准。本地项目清单和导出记录已加入 `.gitignore`。

## 执行审查

项目必须有 `crew/crew.json`、任务要求和交接包，完整示例见 [examples/demo-project](examples/demo-project)。`single` 仅 DSH，`double` 同时要求 DSH 和 Codex。

DSH 需有 `headless` profile；其配置应包含 `@deepseek-ai/dsh-base` 和 `@deepseek-ai/dsh-headless` bundles。先确认 `dsh --profile headless --version` 可运行。Codex CLI 需已登录且支持只读沙箱、临时会话及 JSON schema 输出。

```sh
# 从你的凭据管理器设置，不要写进仓库
export DEEPSEEK_API_KEY='你的 DeepSeek API key'

python scripts/crew.py --project /absolute/path/to/project snapshot task-01
python scripts/crew.py --project /absolute/path/to/project review task-01 --by dsh
# double 任务还需运行：
python scripts/crew.py --project /absolute/path/to/project review task-01 --by codex
python scripts/crew.py --project /absolute/path/to/project gate task-01
```

从 snapshot 到 gate 完成期间，暂停修改被审文件、配置、交接包和任务要求。指纹检查只能检测扫描范围内最终保留的差异；不能证明审查期间从未写入，也不能替代操作系统权限隔离。

`gate` 退出码：`0` 放行、`1` 退回或失效、`2` 等待、`3` 用户裁决。`review` 退出码 `0` 包含通过和退回两种结果，不能据此放行；以 `gate` 为准。审查员提出的 blocker 由原审查员确认关闭，只有本轮正式放行才提交关闭。

`approve` 用于规则变更或追加轮次，`decide --accept` 用于记录用户接受现状；均需用户明确决定，不能由代理自行批准。

### 可选环境变量

| 变量 | 用途 |
| --- | --- |
| `CREW_DSH_BIN` / `CREW_CODEX_BIN` | 覆盖 CLI 命令，默认从 PATH 查找 |
| `DEEPSEEK_API_KEY` | DSH API key |
| `CREW_KEYCHAIN_SERVICE` | macOS 可选钥匙串服务名，默认 `crew-review-deepseek-api`，账号 `deepseek` |
| `CREW_STUDIO_DIR` | 看板目录；默认使用仓库内 `studio/`，设为空字符串可关闭运行标记 |

作者本地使用 DSH `0.2.1-rc.2`；其他 CLI 版本的事件结构和权限行为需重新验证。实际运行还应保留只读探针日志，不以模型自述证明权限生效。

## 作为 Skill 使用

仓库根目录包含 [SKILL.md](SKILL.md)。将整个仓库放到支持此格式的代理技能目录（例如 `~/.claude/skills/crew-review/`），保留 `scripts/`、`prompts/`、`schema/` 的相对位置。已安装旧版时先备份，避免覆盖正在使用的配置和记录。

工作约定包含交接包格式、单审/双审、复审和用户裁决。不同客户端是否自动识别技能，需按其加载规则确认；也可直接使用命令行。

## 测试

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q -p no:cacheprovider
python -m unittest discover -s studio/tests -v
```

默认使用假审查员，不调用模型。真实测试默认跳过；手动设置 `CREW_REAL=1` 才会运行 DSH 权限探针和真实审查，原始记录保存在 `docs/acceptance-logs/`，不提交 GitHub。

## 目录

```text
SKILL.md          代理工作约定
scripts/          调度器与审查记录校验
prompts/          DSH / Codex 审查指令
schema/           结构化结论格式
studio/           深色看板、只读服务、Word 报告
examples/         不含私人数据的最小项目
tests/            调度器测试与假审查员
```

## 开源许可

本项目采用 [MIT License](LICENSE)，允许商用、修改和分发，需保留版权声明和许可文本。第三方依赖遵循各自许可证。

## 公开演示的发布与到期

`python scripts/build_demo.py` 只读取界面模板和 `demo.json`，在 `_site/` 生成静态网页；不会打包本地配置、项目记录、日志或凭据。公开版不加载连接本机接口的 `studio-records.js`。

GitHub Pages 使用 `.github/workflows/pages.yml` 发布。每天 11:37（北京时间）检查截止时间；到期后在服务端用结束页替换全部演示文件，后续发布也不会恢复过期演示。GitHub 定时任务可能延迟或漏跑，下一次检查会重试，不能保证秒级下线。

修改 `demo.json` 的截止时间再发布可调整开放周期。源码仍可下载并在本地运行。
