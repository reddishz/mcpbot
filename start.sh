#!/bin/sh
# keeplocal 启动脚本：把运行数据与部署配置放在项目目录之外，避免与代码混在一起。
#
#   ./start.sh              启动（默认 http://127.0.0.1:8100）
#   ./start.sh --help       运行期参数与配置段说明
#   KEEPLOCAL_WORKDIR=/path ./start.sh
#
# 首次运行会在项目同级目录生成 keeplocal-work/：keeplocal.yaml（权限 600）、data/ 记录域与站点凭据
# 文件（keeplocal.tokens.yaml，权限 600）、keeplocal.log。凭据不入配置文件，更换后重启生效。

set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKDIR=${KEEPLOCAL_WORKDIR:-$(dirname -- "$PROJECT_DIR")/keeplocal-work}
ENV_NAME=${KEEPLOCAL_CONDA_ENV:-keeplocal}
MODEL=${KEEPLOCAL_MODEL:-qwen3.5:0.8b}
CONFIG="$WORKDIR/keeplocal.yaml"

if [ ! -d "$PROJECT_DIR/.svn" ] && [ ! -d "$PROJECT_DIR/src/keeplocal" ]; then
    echo "启动脚本不在 keeplocal 项目根目录：$PROJECT_DIR" >&2
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

TOKENS="$WORKDIR/data/keeplocal.tokens.yaml"

if [ ! -f "$CONFIG" ]; then
    sed -e "s|REPLACE-WITH-MODEL-TAG|$MODEL|" \
        "$PROJECT_DIR/keeplocal.yaml.example" > "$CONFIG"
    chmod 600 "$CONFIG"
    echo "已生成部署配置 $CONFIG（模型 $MODEL，权限 600）。"
fi

if [ ! -f "$TOKENS" ]; then
    # 站点凭据由底层框架在首次启动时写入凭证文件，本脚本不代管其内容
    echo "首次启动会生成站点凭据 $TOKENS（权限 600）；登录用 Key 取其中 admin 条目的 secret。"
fi

# 切到工作目录后由 --workdir 再定位一次：wcore 会据此查找 keeplocal.yaml 并写入日志与记录域
cd "$WORKDIR"
exec "$PYTHON" -m keeplocal.app --workdir "$WORKDIR" "$@"
