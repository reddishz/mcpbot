# mcpbot

面向个人自托管环境的单用户 Web Agent：浏览器（手机 / PC）→ 服务端 → 外部 Ollama 与业务端 MCP Server。

## 设计基线

本仓库的公开镜像只包含代码与运行材料。完整设计基线（L0 战略与愿景 · L1 利益相关者需求 · L2 系统需求 · L3 概念架构与 ADR · L4 逻辑与接口设计 · L5 详细设计 · L6 验证与确认）在内部版本库的 `ued/` 目录维护，**不随本镜像发布**，其编码与追溯规则由 design-doc 规范约束。

因此本文件不描述需求与验收口径，只说明如何跑起来；代码中的注释与文档字符串按 `IF` / `FLW` / `CON` 等细项编码标注其依据，这些编号在公开仓库内没有对应文档。

## docs/ 的定位

[`docs/`](./docs/) 是**历史参考资料**，保留有价值但**不具约束力**：其中描述的产品形态（Android 原生载体、特定业务领域工具、具体框架选型）已被现行基线取代，不得据其实现。

仓库根目录不另立路线图。

## 运行

当前进度是 M0：站点认证 + 一次非流式对话。MCP 工具接入、增量流式呈现与记忆尚未实现。

一次性准备（专用环境，Python 3.13 单运行时）：

```bash
conda create -n mcpbot python=3.13 -y
conda run -n mcpbot pip install -e libs/wcore      # 共享基础库，未发布到索引，需单独取得
conda run -n mcpbot pip install -e ".[serve]"
```

`libs/wcore` 在原始仓库里是一条指向内部共享库的外部引用，**不随本镜像发布**：没有它则 `import wcore` 失败、应用无法启动。

启动一律用根目录的 `./start.sh`。工作目录与项目代码分开：默认使用与本项目同级的 `../mcpbot-work`，配置、记录域与运行日志都落在那里，首次运行会按 `mcpbot.yaml.example` 生成配置、随机填入站点 Key 并只打印一次。

```bash
./start.sh                       # 首次会打印本站凭据
./start.sh --help                # 列运行期与配置参数
MCPBOT_WORKDIR=~/srv/mcb MCPBOT_MODEL=qwen3.5:2b ./start.sh
```

`MCPBOT_WORKDIR` / `MCPBOT_CONDA_ENV` / `MCPBOT_MODEL` 分别改工作目录、环境名与默认模型；其余参数原样转发给应用。站点 Key、上游凭据与预算取值写在该目录的 `mcpbot.yaml` 里，取值范围逐项见 `mcpbot.yaml.example` 的注释。

启动核对任一项失败即拒绝进入可服务状态，并给出可定位到段的单行诊断：未找到部署配置文件、日志驱动通知出站通道未显式关闭、必填缺失或取值越界、记录域已被另一进程独占、Ollama 段不可达。


| 属性 | 值 |
|------|----|
| 作者 | 三恒移俗 |
| 创建日期 | 2026-09-23 |
| 最后更新 | 2026-09-24 |
