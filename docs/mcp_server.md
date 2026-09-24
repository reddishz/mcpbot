🥇 推荐 1：Microsoft Learn MCP（最稳定，官方）

    URL：https://learn.microsoft.com/api/mcp
    认证：不需要任何密钥
    能力：读取微软文档、代码示例搜索；标准 Streamable HTTP
    适合：协议握手、session、tools/list 基础调试

    浏览器直接访问会返回 405，必须 POST JSON-RPC，属于正常现象。

🥇 推荐 2：mcpplaygroundonline Mock MCP（调试首选，mock 服务）
专门用来做 MCP 集成测试，可控返回结果，无 auth

    Complex（带 4 个工具，最推荐）：https://mcpplaygroundonline.com/mcp-complex-server
    Echo：https://mcpplaygroundonline.com/mcp-echo-server
    Error 模拟：https://mcpplaygroundonline.com/mcp-error-server
    特点：支持新旧 MCP 协议版本，可测试异常分支、初始化握手。
