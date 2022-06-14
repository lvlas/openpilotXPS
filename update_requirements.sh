#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"
cd $DIR
ls
if ! command -v "pyenv" > /dev/null 2>&1; then
  echo "pyenv install ..."
  curl -L https://github.com/pyenv/pyenv-installer/raw/master/bin/pyenv-installer | bash
  export PATH=$HOME/.pyenv/bin:$HOME/.pyenv/shims:$PATH
fi

export MAKEFLAGS="-j$(nproc)"

PYENV_PYTHON_VERSION=$(cat .python-version)
if ! pyenv prefix ${PYENV_PYTHON_VERSION} &> /dev/null; then
  # no pyenv update on mac
  if [ "$(uname)" == "Linux" ]; then
    echo "pyenv update ..."
    pyenv update
  fi
  echo "python ${PYENV_PYTHON_VERSION} install ..."
  CONFIGURE_OPTS="--enable-shared" pyenv install -f ${PYENV_PYTHON_VERSION}
fi
eval "$(pyenv init --path)"

echo "update pip"
pip install pip==21.3.1
pip install poetry

if [ -d "./xx" ]; then
  export POETRY_SYSTEM=1
fi

if [ -z "$POETRY_SYSTEM" ]; then
  echo "PYTHONPATH=${PWD}" > .env
  RUN="poetry run"
else
  poetry config virtualenvs.create false
  RUN=""
fi

echo "pip packages install..."
poetry install
pyenv rehash

echo "pre-commit hooks install..."
shopt -s nullglob
for f in .pre-commit-config.yaml */.pre-commit-config.yaml; do
  cd $DIR/$(dirname $f)
  if [ -e ".git" ]; then
    $RUN pre-commit install
  fi
done
