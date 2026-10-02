# MCP 测试与验证端点

本文件记录可用于真实联调的外部 MCP Server，属参考材料，不作为设计依据。

## 默认真实端点：sxo BMC

第一个用于**线上校准**的真实业务 MCP Server：提供真实数据、真实凭据校验和真实权限判定，适合验证协议握手、能力发现、工具调用与故障归类。

| 项 | 值 |
|----|----|
| 地址 | `https://sxo.cc/mcp` |
| 认证 | 请求头 `X-API-Key`（**不是** `Authorization: Bearer`） |
| 只读测试 Key | `fd525844f5610202ff0e5390d877dd77` |
| 协议修订 | `2026-07-28`，stateless，**无 `initialize` 握手** |
| 承载 | 仅 JSON，不支持 SSE / 长订阅（`GET /mcp` 返回 400） |
| 服务端标识 | `sxo-bmc` 1.0.0 |

该 Key 已明确为公开测试用途，仅含读权限，写入类工具会以业务拒绝返回。

### 配置写法

```yaml
mcp:
  services:
    - service_id: sxo
      url: "https://sxo.cc/mcp"
      api_key: "fd525844f5610202ff0e5390d877dd77"
      allowed_tools:
        - BmcGetList
        - BmcGetSearch
        - BmcGetDetail
```

`allowed_tools` 是「服务标识 → 工具标识集合」的精确匹配，空集合表示该服务的工具不开放给模型选择。**上游改名工具会使既有清单的交集直接变为空**（本端点 2026-10 就从 `bmc_list` 一类蛇形名改为 `BmcGetList` 一类驼峰名），此时服务状态仍为可用，但没有任何工具可选——排查时先看交集，不要先怀疑上游故障。

### 工具清单

| 工具 | 用途 | 读写 |
|------|------|------|
| `BmcGetList` | 分页列出商业模式画布 | 读 |
| `BmcGetSearch` | 按关键词搜索 | 读 |
| `BmcGetDetail` | 按 id 取详情 | 读 |
| `BmcPostCreate` | 创建 | 写 |
| `BmcPutUpdate` | 更新 | 写 |
| `BmcDelete` | 删除 | 写 |

六个工具均声明 `annotations`（`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`）与 `outputSchema`。这些提示由服务端自述，只用于呈现，不作为本站的授权依据。

### 结果与错误的三层形态

这是本端点最有校准价值的部分：三类失败分别出现在三个不同的标准位置上。

| 层 | 场景 | 传输层 | 载体 |
|----|------|--------|------|
| 门禁 | Key 缺失或无效 | `401` + `WWW-Authenticate` | 无 JSON-RPC 包，错误说明只在 HTTP 状态与响应体 |
| 协议 | 报文语法错、`jsonrpc` 成员非法、`arguments` 非对象 | `400` | 同上 |
| 协议 | 缺 `Mcp-Method`、头与 body 不符、协议版本矛盾 | `417` | 同上 |
| 协议 | 未知方法 | `404` | 同上 |
| 协议 | `_meta` 缺失、参数校验失败、工具名不存在 | `422` | 同上 |
| 工具 | 业务拒绝（记录不存在、权限不足） | `200` | `result.isError=true` + `content` + `structuredContent` |

两点必须注意：

1. **鉴权失败的证据只存在于 HTTP 状态**。协议没有为凭据被拒规定带内 JSON-RPC 载体，而官方 Python SDK 在收到「非 2xx 且响应体不是 JSON-RPC 报文」时会合成一个 `-32603` 并把 HTTP 状态丢弃。因此客户端必须自建 HTTP 客户端并在响应钩子里保留状态码，否则「上游鉴权失败」与「上游服务不可用」会被压成同一个类别。
2. **私有错误字段不参与归类**。该端点把错误说明放在自定义响应体字段里，规范允许非 2xx 的响应体形态不作规定，客户端按 HTTP 状态与 `isError` 归类即可，不应解析私有编码来推断更细的类别。

`structuredContent` 与 `content[0].text` 同为服务端信封，`data` 内为业务载荷。本站把它当作不透明结果透传给编排，不解析其字段判定业务成功。

### 已知不影响联调的行为

- `limit` 越界（如 `9999`、`-1`、`0`）被静默钳制到 `1..100` 或默认值，不报错。
- `integer` 属性带字符串 `pattern`，对整型不生效。
- `notifications/*` 返回 `400` 且无响应体；stateless 联调不需要发送通知。
- 请求缺少 `id` 时按通知处理，返回 `202` 空响应体。

## 公共端点（仅用于首轮握手校准）

| 端点 | 地址 | 说明 |
|------|------|------|
| Microsoft Learn MCP | `https://learn.microsoft.com/api/mcp` | 无认证，标准 Streamable HTTP，适合 `tools/list` 与 session 基线调试。浏览器直接访问返回 405，必须 POST JSON-RPC，属正常现象。 |
| mcpplaygroundonline Echo | `https://mcpplaygroundonline.com/mcp-echo-server` | 无认证，可控返回。 |
| mcpplaygroundonline Complex | `https://mcpplaygroundonline.com/mcp-complex-server` | 无认证，4 个工具，支持新旧协议版本。 |
| mcpplaygroundonline Error | `https://mcpplaygroundonline.com/mcp-error-server` | 无认证，用于观察错误形态。 |

公共端点只用于确认握手与协议版本协商正确，不作为故障归类的判据来源——它们的错误形态不代表真实业务端。

## 异常注入

超时、连接中断、超大结果、鉴权失效等**异常分支一律用本地替身**验证，不向公共端点或真实业务端注入异常流量。本地替身可控制延迟、断流与响应体积，是唯一能稳定复现「结果不确定」情形的通道。
