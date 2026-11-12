#!/usr/bin/env bash
# Gradescope image setup. Runs once when the autograder image is built.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential python3 python3-pip
pip3 install --no-cache-dir -r /autograder/source/requirements.txt

# Unprivileged account for compiling and running student code.
id student >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin student

# Project-specific setup hook, if the project ships one.
PROJECT="$(cat /autograder/source/PROJECT)"
if [ -x "/autograder/source/projects/$PROJECT/setup.sh" ]; then
  "/autograder/source/projects/$PROJECT/setup.sh"
fi
