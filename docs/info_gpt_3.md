对，你现在这个判断**大方向成立，但“手机一定比普通 PC 慢”已经不能简单这么说了**。2026 年的旗舰 Android 已经有专门的 NPU/加速路径，跑 1B～4B 小模型可以达到相当实用的速度；真正的问题是：**和一台有 GPU 的 PC 比，手机仍然通常没有优势，而且持续运行有功耗/温控约束。**

### 先给结论

对于你这个 **通用 MCP Agent**，我建议：

> **第一版不要把 Ollama 放进 Android。**
>
> Android 只实现 Agent Runtime + MCP Client；Ollama 先放 Ubuntu/PC。
>
> 但架构上保留 `LLMProvider` 抽象，将来可以增加本地模型。

这样你不会因为“离网”这个需求过早承担移动端 LLM 的复杂性。

---

## 1. 手机到底有多慢？

这个差距取决于模型和硬件，不能简单说“手机慢”。

现在 Android 已经有专门的 LLM benchmark。MLPerf Mobile v6.0 已经加入了 Android 的 LLM 测试，包括 Llama 3.2 1B、3B、Llama 3.1 8B，并且 Snapdragon 8 Elite Gen 5 已经支持 NPU 加速 8B 模型。:chatgpt-content-reference{index="0"}

`llama.cpp` 目前也已经支持 Snapdragon：

```text
CPU
Adreno GPU
Hexagon NPU
```

三种后端。官方示例中，Gemma 2 2B 在 Snapdragon 上通过 Hexagon HTP 可以达到约 **47.6 tok/s decode**；Llama 3.2 1B 示例约 **18.4 tok/s**。:chatgpt-content-reference{index="1"}

所以：

```text
手机
1B～3B
↓
完全可以实用
```

已经不是以前那种“手机只能勉强跑”的状态。

---

## 2. 但你的 Agent 场景和普通聊天不完全一样

你不是做：

> 手机离线聊天机器人

而是：

```text
用户
 ↓
LLM
 ↓
Tool selection
 ↓
MCP
 ↓
Tool result
 ↓
LLM
 ↓
最终回答
```

这会产生多个 LLM inference cycle。

例如：

```text
用户问题
 ↓
第一次推理：选择工具
 ↓
MCP
 ↓
第二次推理：理解结果
 ↓
可能再次调用工具
 ↓
第三次推理
 ↓
最终回答
```

所以即使手机模型有：

```text
20 tok/s
```

实际用户感受到的延迟仍然可能明显。

而且 Agent 最重要的不一定是生成速度，而是：

**Tool Calling 的可靠性。**

这也是为什么我不建议为了“离网”直接把模型塞进 Android。

---

# 3. PC 的优势实际上更明显

如果你的 Ubuntu 是：

```text
普通 CPU
```

那么旗舰手机未必输得特别惨。

但如果 Ubuntu PC 有：

```text
NVIDIA GPU
```

那就基本不用比较了。

尤其：

```text
Qwen 3B / 4B
Qwen 7B / 8B
```

PC GPU 的吞吐和内存容量都会明显占优。

所以比较应该是：

```text
旗舰 Android NPU
       VS
PC CPU
```

和：

```text
旗舰 Android NPU
       VS
PC GPU
```

完全是两回事。

---

# 4. 手机真正的问题其实是“持续负载”

这是我认为你这个项目更应该考虑的。

PC：

```text
LLM
 ↓
持续跑
 ↓
GPU 100W+
```

问题主要是耗电，但：

- 散热好
- 不影响用户使用
- 内存大

手机：

```text
LLM
 ↓
NPU/GPU/CPU
 ↓
持续发热
 ↓
降频
 ↓
耗电
```

所以一次问答没问题。

但如果你的 Agent 连续运行几十次：

```text
Tool call
→ LLM
→ Tool call
→ LLM
→ ...
```

手机体验会越来越差。

---

# 5. 离网是它唯一真正的大优势

这个优势其实非常明确：

### PC/远程 Ollama

```text
Android
   ↓
网络
   ↓
Ollama
```

断网：

```text
❌ Agent 基本不能工作
```

### Android 本地 LLM

```text
Android
 ├── Agent
 ├── LLM
 └── MCP
```

断网：

```text
✅ LLM
✅ Agent
✅ 本地 MCP
```

所以如果你的目标是：

> **真正完全离线的个人 Agent**

那么本地 LLM 是有价值的。

---

# 6. 但这里又出现一个问题：MCP 也要离线

如果你只是：

```text
Android
  ↓
本地 LLM
  ↓
远程 MCP Server
```

那么：

```text
没有网络
   ↓
LLM 可以工作
   ↓
MCP ❌
```

所以“离网能力”实际上应该定义成：

### Level 1

```text
本地 LLM
+
远程 MCP

断网：
可以聊天
不能调用远程工具
```

### Level 2

```text
本地 LLM
+
本地 MCP
+
本地数据/工具

断网：
完整 Agent
```

如果你真正想做的是 Level 2，那么 Android 本地 LLM 才有更大的意义。

---

# 7. 因此我建议你的架构不要绑定 Ollama

这是现在最重要的设计决策。

不要：

```text
Agent
 ↓
Ollama
```

而应该：

```text
AgentRuntime
      ↓
    LLM API
      │
      ├── OllamaProvider
      │
      └── AndroidLocalProvider
```

例如：

```kotlin
interface LlmProvider {
    suspend fun chat(
        messages: List<Message>,
        tools: List<Tool>
    ): LlmResponse
}
```

Ubuntu：

```text
OllamaProvider
 ↓
Ollama HTTP
```

Android 第一版：

```text
OllamaProvider
 ↓
局域网/远程 Ollama
```

以后：

```text
AndroidLocalProvider
 ↓
llama.cpp / MediaPipe / 其他 Android runtime
```

这样**Agent/MCP 完全不用改。**

---

# 8. 这也改变一下之前的实践方案

你刚才说得对：

> **方案主要是支持通用 MCP，不涉及具体业务。**

那么第一阶段应该非常纯粹：

```text
Ubuntu
│
├── Agent
│
├── Ollama
│
└── MCP Client
       │
       ▼
   Generic MCP Server
```

只验证通用 MCP：

```text
initialize / protocol handling（如果针对旧客户端）
tools/list
tools/call
resources/list
resources/read
prompts/list
prompts/get
notifications
错误处理
认证
```

不要塞 Binance。

然后 Android：

```text
Android
│
├── Agent Runtime
│
├── MCP Client
│
└── OllamaProvider
        │
        ▼
     Ubuntu Ollama
```

最后再增加：

```text
AndroidLocalProvider
        │
        ▼
   Local LLM Runtime
```

---

## 最后给你一个明确建议

现在**不要因为“离网”就把 Ollama 搬到手机**。

先把架构做成：

```text
             ┌── Ollama / PC
             │
AgentRuntime ┤
             │
             └── Local LLM / Android
```

也就是：

> **LLM 是可替换后端，MCP 是独立能力层。**

这样以后如果你发现手机上的 Qwen 3B/4B 已经足够快，就增加本地 Provider；如果发现实际体验不好，直接继续使用 PC Ollama。

而且目前 Android 本地 LLM 的能力确实正在快速提升：MLPerf 已经开始标准化 Android LLM benchmark，`llama.cpp` 也已经针对 Snapdragon 的 GPU/NPU 做了专门后端。:chatgpt-content-reference{index="2"}

**所以现在没必要做“Android 是否能跑”的架构赌注。把它留成 Provider 插件即可。**
---
什么级别的cpu能达到这么水准，目前市面上占比多少，是否是主流。 是否可以简单通过apk安装方式实现而无需专业级如adb的操作。 貌似我的pc都没这个速度。我的pc配置：操作系统： Kubuntu 26.04 LTS
KDE Plasma 版本： 6.6.6
KDE 程序框架版本： 6.24.0
Qt 版本： 6.10.2
内核版本： 7.0.0-31-generic (64 位)
图形平台： Wayland
处理器： 12 × Intel® Core™ i7-10750H CPU @ 2.60GHz
内存： 64 GiB 内存 (62.7 GiB 可用)
图形处理器 1： Intel® UHD Graphics
图形处理器 2： NVIDIA GeForce GTX 1650 Ti
制造商： TIMI
产品名称： Redmi G

---

对，你这个感觉是对的：**47.6 tok/s 这个数字不能理解成“普通手机 CPU 的水平”**。它主要是 **Snapdragon 高端 SoC 的 Hexagon NPU/HTP 加速结果**，不是 CPU 单独跑出来的。

### 1. 47.6 tok/s 大概是什么硬件级别？

你引用的 Gemma 2 2B / 47.6 tok/s，是类似：

- Snapdragon 8 Elite / 8 Elite Gen 5 这一档
- Hexagon HTP/NPU
- GGUF 量化模型
- llama.cpp 专门的 Snapdragon 后端

也就是说，它实际上是：

> **CPU + Adreno GPU + Hexagon NPU 的异构计算**

而不是“一个手机 CPU 有这么快”。

现在 llama.cpp 的 Snapdragon 后端已经明确支持 CPU、Adreno GPU、Hexagon NPU 三种后端。:chatgpt-content-reference{index="0"}

所以拿这个数字和你 **i7-10750H CPU** 的 tok/s 直接比较，其实不是完全公平的。

---

## 2. 你的 PC 为什么可能还没有它快？

你的机器：

> i7-10750H + GTX 1650 Ti 4GB

这里其实有个非常关键的问题：

**你的 GTX 1650 Ti 理论上完全有机会跑得比 i7-10750H CPU 快，但 4GB 显存会严重限制模型/上下文和 GPU offload。**

而且如果你目前是：

```text
Ollama
   ↓
CPU
   ↓
i7-10750H
```

那就完全可能只有十几 tok/s，甚至更低。

而手机测试是：

```text
Gemma 2 2B
      ↓
Hexagon HTP
      ↓
几十 tok/s
```

这是两种完全不同的计算路径。

另外，47.6 tok/s 本身也不是一个“所有 Snapdragon 旗舰都能达到”的固定性能指标，而是特定模型、量化、后端和测试条件下的结果。

---

# 3. 什么手机才能达到这个级别？

可以粗略分三档：

| 手机 SoC | 本地 LLM 情况 |
|---|---|
| Snapdragon 8 Gen 1/2 | 已经可以认真玩本地 LLM |
| Snapdragon 8 Gen 3 / 8 Gen 2 + NPU | 2B~4B 很有实际价值 |
| Snapdragon 8 Elite / Gen 5 | 开始进入“本地 Agent 真正可用”的级别 |

甚至现在 llama.cpp 已经有针对 Snapdragon 8 Gen 3 和 7+ Gen 3 的 GPU/NPU 构建方式。:chatgpt-content-reference{index="1"}

所以**并不要求必须买最新一代旗舰**。

但如果你的目标不是“能跑”，而是：

> 手机上的 Agent + MCP + tool calling，要感觉比较流畅

那么我会把目标硬件放在：

**Snapdragon 8 Gen 3 以上。**

尤其是 8 Elite / 更新一代旗舰。

---

# 4. 这种手机现在是不是主流？

这里要区分两个“主流”。

### Android 市场主流

不是。

大量 Android 手机仍然是：

- Snapdragon 6/7 系列
- MediaTek 中低端 SoC
- 老 Snapdragon 8 系列

所以不能假设用户手机都有这种 NPU 性能。

### 高端 Android 主流

这个趋势已经非常明显。

旗舰 Android 基本都已经把 NPU/AI accelerator 当成标准硬件。

因此如果你的 APK 定位是：

> **面向高端 Android 手机的本地 Agent**

完全合理。

但如果目标是：

> **所有 Android 手机都能本地运行 2B~4B LLM**

那就不现实。

---

# 5. 最重要的问题：能不能像普通 APK 一样安装？

**可以，但这里要区分“你自己测试 llama.cpp”和“你最终开发的 Agent APK”。**

现在官方 llama.cpp Android 示例已经提供了 Android GUI binding，可以直接作为 Android Studio 项目构建 APK。它甚至可以从 Android 的 `ContentResolver` / 文件 URI 读取 GGUF，然后通过 `AiChat` / `InferenceEngine` 加载模型。:chatgpt-content-reference{index="2"}

所以最终用户完全可以：

```text
下载 APK
    ↓
安装
    ↓
选择 GGUF 模型
    ↓
运行
```

**不需要 ADB。**

ADB 主要是目前 llama.cpp 官方开发/测试流程中的东西，例如：

```bash
adb push ...
adb shell ...
```

官方 Snapdragon 文档目前确实仍然大量采用这种方式进行开发测试。:chatgpt-content-reference{index="3"}

但那是：

> **开发者部署方式**

不是：

> **最终 App 用户使用方式**

---

# 6. 甚至你的 Agent APK 可以把这些全部封装掉

最终可以做到：

```text
┌──────────────────────────────┐
│          Agent APK           │
│                              │
│  Agent Runtime               │
│       │                      │
│       ├── MCP Client         │
│       │                      │
│       └── LLM Provider       │
│              │               │
│       AndroidLocalProvider   │
│              │               │
│       llama.cpp / NPU        │
│                              │
│  Model Manager               │
│       │                      │
│       └── Qwen/Gemma GGUF    │
└──────────────────────────────┘
```

用户只需要：

1. 安装 APK
2. 下载/选择模型
3. 开始使用

APK 自己负责：

- 判断 CPU/SoC
- 判断是否支持 NPU
- 选择 CPU/GPU/NPU backend
- 加载 GGUF
- 管理模型
- 管理上下文
- MCP tool calling

用户完全不需要知道：

```text
adb
NDK
Hexagon SDK
CMake
llama.cpp
HTP
```

---

## 7. 但这里有一个很重要的坑

**“普通 APK 安装” ≠ “直接获得 Hexagon NPU 性能”。**

这是你现在设计 Android Agent 时最需要注意的地方。

因为：

```text
普通 APK
   ↓
Kotlin
   ↓
llama.cpp
   ↓
CPU
```

很容易。

但是：

```text
APK
 ↓
llama.cpp
 ↓
Hexagon HTP
 ↓
NPU
```

需要正确集成：

- ARM native library
- Snapdragon backend
- Hexagon/HTP library
- 对应 SoC 架构
- Android 权限/运行环境
- 不同 Snapdragon 代际兼容

目前官方 llama.cpp 的 Snapdragon 构建仍然涉及 Android NDK、OpenCL、Hexagon SDK 等开发工具链。:chatgpt-content-reference{index="4"}

**这部分是你开发者需要解决的，不是用户需要解决的。**

---

# 8. 对你的 PC，我反而建议先测一下

你的 PC 很适合拿来做一个基准。

你的硬件：

```text
i7-10750H
GTX 1650 Ti 4GB
64 GB RAM
```

实际上非常适合测试：

```text
Qwen 2.5/3 1.5B
Qwen 3 4B
Gemma 2 2B
Llama 3.2 1B
```

尤其应该比较：

```text
A. i7 CPU
B. GTX 1650 Ti
C. 手机 Snapdragon NPU
```

而不是只比较 Ollama 默认结果。

你现在如果告诉我**你目前 Ollama 使用的具体模型和 `ollama ps` 输出**，我可以直接判断为什么你的 PC 感觉还不如那个 47.6 tok/s，以及你的 GTX 1650 Ti 实际有没有参与推理。

---
权衡后，接受你的建议，不在手机上安装ollama.  那么比如需要一台服务器来部署。这样子综合考虑吃下来，全部逻辑都可以放到服务器上。 手机上只需要打开后台提供的网页即可。可以做到手机上最轻松。 那么这里存在的问题就是如何部署一台支持聊天（有简单的历史记录，历史记忆最好），和mcp能力的后端服务。给出你的建议。
---

对，我认为这个调整之后，整体架构反而**明显简单了**。

你的目标可以收敛成：

> **服务器 = Agent 大脑 + LLM + MCP + 数据/记忆；手机 = 一个很薄的 Web Client。**

而且这比“Android 原生 Agent + 本地 LLM”更适合你目前这个项目。

## 我建议的最终架构

```text
                    Internet / LAN
                         │
                         ▼
              ┌─────────────────────┐
              │      Web Browser    │
              │   Android / PC      │
              └──────────┬──────────┘
                         │ HTTPS
                         ▼
┌────────────────────────────────────────────────┐
│                 Agent Server                   │
│                                                │
│  Web UI ────────┐                              │
│                 ▼                              │
│           Agent Runtime                        │
│                 │                              │
│       ┌─────────┴─────────┐                    │
│       ▼                   ▼                    │
│   LLM Provider        Memory Manager            │
│       │                   │                    │
│       ▼                   ▼                    │
│    Ollama              SQLite                  │
│       │                                        │
│       ▼                                        │
│     LLM                                        │
│       │                                        │
│       ▼                                        │
│    Tool / MCP Router                           │
│       │                                        │
│       ├──────── MCP Server A                   │
│       ├──────── MCP Server B                   │
│       └──────── MCP Server C                   │
│                                                │
└────────────────────────────────────────────────┘
```

---

# 一、服务器应该拆成 5 个部分

我不建议现在引入什么大型 Agent 平台。

直接做一个自己的轻量服务：

### ① Web UI

手机浏览器访问：

```text
https://agent.example.com/
```

提供：

- 对话框
- 流式输出
- 新建会话
- 历史会话
- 设置
- MCP 状态

手机甚至不需要安装 APK。

如果只是个人使用，Web UI 可以非常简单。

---

### ② Agent Runtime

这是整个系统的核心。

负责：

```text
用户问题
   ↓
读取历史
   ↓
构造 Prompt
   ↓
调用 LLM
   ↓
发现 tool call
   ↓
调用 MCP
   ↓
拿到 tool result
   ↓
再次调用 LLM
   ↓
最终回答
   ↓
保存会话
```

也就是我们之前讨论的 Agent Loop。

**这里我仍然建议 Python + PydanticAI。**

PydanticAI 本身已经有 MCPToolset，可以直接连接 Streamable HTTP MCP server。:chatgpt-content-reference{index="0"}

所以 Ubuntu 上验证过的东西，可以基本直接变成服务器生产代码。

---

# 二、Ollama 就放服务器

这一点现在可以彻底确定：

```text
Agent Runtime
      │
      ▼
Ollama
      │
      ▼
Qwen / Gemma / Llama
```

Ollama 原生支持 tool calling，因此 Agent 不需要自己解析什么奇怪的模型输出。:chatgpt-content-reference{index="1"}

而且 Ollama 的模型加载、GPU/CPU、模型切换等问题全部留在服务器。

手机完全不关心。

---

# 三、Memory 不要一开始搞复杂

你说的：

> 简单历史记录，历史记忆最好

我建议分成两个层次。

### Conversation History

直接 SQLite：

```text
conversation
├── id
├── title
├── created_at
└── updated_at

message
├── id
├── conversation_id
├── role
├── content
└── created_at
```

这样马上就能做到：

```text
今天
 ├── BTC 分析
 ├── MCP 开发
 └── Zcash

昨天
 ├── Android Agent
 └── 网格策略
```

---

### Long-term Memory

**第一版不要上向量数据库。**

增加一个：

```text
memory
├── id
├── user_id
├── category
├── content
├── created_at
└── updated_at
```

例如：

```text
category = preference
content  = 用户偏好回答简洁，避免重复背景

category = project
content  = Agent 项目采用服务器 Ollama + MCP 架构
```

Agent 每次请求只取**相关的小部分 Memory**。

第一版甚至可以：

```text
最近 N 条对话
+
用户长期 Memory
```

已经足够。

以后真的需要语义检索，再加：

```text
Embedding
   ↓
Vector DB
```

不要一开始就引入 pgvector / Qdrant / Chroma。

---

# 四、MCP 放在 Agent Server 这一侧

这里是我比较建议你坚持的结构：

```text
Agent Runtime
      │
      │ MCP Client
      ▼
MCP Server
```

而不是：

```text
Web Browser
      ↓
MCP
      ↓
Server
```

手机只是 UI。

这样以后换：

```text
Android Browser
iPhone Browser
PC Browser
桌面 App
API Client
```

Agent 后端完全不用变化。

---

# 五、MCP 可以和 Web 服务放在同一个服务器

这个非常方便。

例如：

```text
https://agent.example.com/

GET  /
      → Web UI

POST /api/chat
      → Chat API

GET  /api/conversations
      → 历史记录

GET  /api/memory
      → Memory

POST /api/auth
      → 登录

POST /api/v1/agent/mcp
      → MCP Streamable HTTP
```

MCP Python SDK 本身支持把 Streamable HTTP MCP App 挂到现有 ASGI 应用，也就是 FastAPI/Starlette/Uvicorn 这一套可以共用。:chatgpt-content-reference{index="2"}

所以没必要：

```text
Web Server
MCP Server
API Server
Memory Server
```

搞四五个独立服务。

**第一版一个进程/一个 ASGI 服务完全够。**

---

# 六、我建议的技术栈

最终可以非常克制：

| 部分 | 技术 |
|---|---|
| Web UI | HTML + JS，或者 React |
| HTTP | FastAPI |
| Agent | PydanticAI |
| LLM | Ollama |
| 模型 | Qwen / Gemma 等 |
| MCP Client | PydanticAI MCPToolset |
| MCP Server | MCP Python SDK |
| MCP Transport | Streamable HTTP |
| Database | SQLite |
| ORM | SQLAlchemy / SQLModel |
| Auth | Bearer Token 或登录 Session |
| Reverse Proxy | Caddy / Nginx |
| TLS | Let's Encrypt |
| 部署 | Ubuntu + systemd |

**不需要 Docker 也能开始。**

如果以后需要部署多实例，再考虑 Docker。

---

# 七、服务器硬件反而值得重新考虑

你之前那个：

> i7-10750H + GTX 1650 Ti 4GB

其实可以直接拿来当第一台 Agent Server。

而且现在不是让它跑手机上的东西，而是：

```text
手机
 ↓
WiFi/Internet
 ↓
你的 PC
 ↓
Ollama
 ↓
GPU/CPU
```

你的 GTX 1650 Ti 虽然只有 4GB，但跑：

- 1B
- 2B
- 3B
- 一些量化 4B

都可以作为第一阶段实验。

而且 **64GB RAM 是非常大的优势**。

以后如果发现：

> 4B/8B 不够快

再换服务器 GPU，而不是重新设计整个 Agent。

---

# 八、这里有一个我认为很重要的设计决定

不要让 Agent Runtime 和 Ollama 绑定。

定义：

```python
class LLMProvider:
    async def chat(...):
        ...
```

然后：

```text
Agent Runtime
       │
       ▼
   LLMProvider
       │
       ├── OllamaProvider
       │
       ├── OpenAIProvider
       │
       └── OtherProvider
```

这样以后你甚至可以：

```text
服务器 Ollama
        ↓
本地模型

或者

服务器
        ↓
OpenAI / Gemini / 其他 API
```

Agent 本身完全不用改。

---

# 九、MCP 也不要写死

同样：

```text
Agent
 │
 └── MCP Client
       │
       ├── MCP Server A
       ├── MCP Server B
       └── MCP Server C
```

配置：

```yaml
mcp_servers:
  - name: xxx
    url: https://xxx/api/v1/agent/mcp
    token: ...

  - name: yyy
    url: https://yyy/api/v1/agent/mcp
    token: ...
```

这样你的 Agent 本身就是一个**通用 MCP Host**。

这也符合 MCP 的设计目标：把“给模型提供上下文/工具”和“LLM 本身”分离。:chatgpt-content-reference{index="3"}

---

# 十、最终我建议你做成这个最小系统

```text
                 手机
                  │
                  │ HTTPS
                  ▼
        ┌───────────────────┐
        │     FastAPI       │
        │                   │
        │   Web UI          │
        │   Chat API        │
        │   Auth            │
        │   History         │
        │   Memory          │
        │        │          │
        │        ▼          │
        │  Agent Runtime    │
        │        │          │
        │   ┌────┴────┐     │
        │   ▼         ▼     │
        │ Ollama     MCP    │
        │              │    │
        └──────────────┼────┘
                       │
             Streamable HTTP
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
           MCP-A     MCP-B     MCP-C

             SQLite
                │
        History + Memory
```

### 第一阶段甚至可以进一步砍掉：

```text
React
Redis
PostgreSQL
Vector DB
Docker
Kubernetes
消息队列
```

**全部不要。**

先做到：

> **一个 Ubuntu 服务器 + FastAPI + PydanticAI + Ollama + MCP + SQLite + 一个手机 Web 页面。**

这已经是一个完整可用的个人 Agent。

另外，MCP 官方 Python SDK 当前已经是 **v2 stable**，支持 Streamable HTTP；生产部署时还需要正确配置 Host/Origin 等传输安全设置，不能直接把开发环境的 localhost 配置暴露到公网。:chatgpt-content-reference{index="4"}

**下一步我建议直接进入实践：:chatgpt-content-reference{index="6"}。**这样可以先在你现有 Kubuntu 机器上完整跑通，再考虑公网 HTTPS 和手机访问。
