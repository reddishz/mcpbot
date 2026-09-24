#!/bin/sh
# mcpbot 启动脚本：把运行数据与部署配置放在项目目录之外，避免与代码混在一起。
#
#   ./start.sh              启动（默认 http://127.0.0.1:8100）
#   ./start.sh --help       运行期参数与配置段说明
#   MCPBOT_WORKDIR=/path ./start.sh
#
# 首次运行会在项目同级目录生成 mcpbot-work/：mcpbot.yaml（权限 600，含本站凭据）、data/ 记录域、mcpbot.log。

set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKDIR=${MCPBOT_WORKDIR:-$(dirname -- "$PROJECT_DIR")/mcpbot-work}
ENV_NAME=${MCPBOT_CONDA_ENV:-mcpbot}
MODEL=${MCPBOT_MODEL:-qwen3.5:0.8b}
CONFIG="$WORKDIR/mcpbot.yaml"

if [ ! -d "$PROJECT_DIR/.svn" ] && [ ! -d "$PROJECT_DIR/src/mcpbot" ]; then
    echo "启动脚本不在 mcpbot 项目根目录：$PROJECT_DIR" >&2
    exit 1
fi

PYTHON=""
for candidate in \
    "$HOME/miniconda3/envs/$ENV_NAME/bin/python" \
    "$HOME/anaconda3/envs/$ENV_NAME/bin/python" \
    "/opt/conda/envs/$ENV_NAME/bin/python" \
    "$WORKDIR/venv/bin/python"
do
    [ -x "$candidate" ] && PYTHON=$candidate && break
done
if [ -z "$PYTHON" ]; then
    CONDA_BASE=$(conda info --base 2>/dev/null || true)
    [ -n "$CONDA_BASE" ] && [ -x "$CONDA_BASE/envs/$ENV_NAME/bin/python" ] && PYTHON="$CONDA_BASE/envs/$ENV_NAME/bin/python"
fi
if [ -z "$PYTHON" ]; then
    echo "未找到可用解释器（conda 环境「$ENV_NAME」或 $WORKDIR/venv）。先执行：$PROJECT_DIR/setup.sh" >&2
    exit 1
fi

mkdir -p "$WORKDIR"
chmod 700 "$WORKDIR"

if [ ! -f "$CONFIG" ]; then
    KEY=$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))')
    sed -e "s|REPLACE-WITH-STATION-KEY|$KEY|" -e "s|REPLACE-WITH-MODEL-TAG|$MODEL|" \
        "$PROJECT_DIR/mcpbot.yaml.example" > "$CONFIG"
    chmod 600 "$CONFIG"
    echo "已生成部署配置 $CONFIG（模型 $MODEL，权限 600）。"
    echo "本站凭据（浏览器登录用，仅此一次打印）："
    echo "    $KEY"
fi

# 切到工作目录后由 --workdir 再定位一次：wcore 会据此查找 mcpbot.yaml 并写入日志与记录域
cd "$WORKDIR"
exec "$PYTHON" -m mcpbot.app --workdir "$WORKDIR" "$@"
