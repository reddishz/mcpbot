#!/bin/sh
# mcpbot 一次性环境准备：建 Python 运行环境并安装依赖（含随仓库附带的 libs/wcore）。
#
#   ./setup.sh                                  conda 建/复用 mcpbot 环境
#   MCPBOT_CONDA_ENV=myenv ./setup.sh           换环境名
#   MCPBOT_USE_VENV=1 ./setup.sh                强制用 venv（无 conda 时自动走这条）
#
# 依赖版本区间一律以 pyproject.toml 为准，本脚本不重复声明取值。

set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_NAME=${MCPBOT_CONDA_ENV:-mcpbot}
PY_VERSION=${MCPBOT_PYTHON_VERSION:-3.13}
WORKDIR=${MCPBOT_WORKDIR:-$(dirname -- "$PROJECT_DIR")/mcpbot-work}

install_deps() {
    # $1 = 解释器绝对路径
    "$1" -m pip install --upgrade pip >/dev/null
    "$1" -m pip install -e "$PROJECT_DIR/libs/wcore" -e "$PROJECT_DIR[serve]"
}

if [ "${MCPBOT_USE_VENV:-0}" != "1" ] && command -v conda >/dev/null 2>&1; then
    if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
        echo "复用 conda 环境 $ENV_NAME"
    else
        echo "创建 conda 环境 $ENV_NAME（Python $PY_VERSION）"
        conda create -y -n "$ENV_NAME" "python=$PY_VERSION"
    fi
    install_deps "$(conda info --base)/envs/$ENV_NAME/bin/python"
    echo "完成。启动：$PROJECT_DIR/start.sh"
    exit 0
fi

PYTHON_BIN=$(command -v "python$PY_VERSION" || command -v python3 || true)
if [ -z "$PYTHON_BIN" ]; then
    echo "未找到 python$PY_VERSION 或 python3，无法建 venv。请先安装 Python $PY_VERSION。" >&2
    exit 1
fi

VENV_DIR="$WORKDIR/venv"
mkdir -p "$WORKDIR"
if [ -x "$VENV_DIR/bin/python" ]; then
    echo "复用 venv $VENV_DIR"
else
    echo "创建 venv $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
install_deps "$VENV_DIR/bin/python"
echo "完成。启动：$PROJECT_DIR/start.sh（会自动使用该 venv）"
