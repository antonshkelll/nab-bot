#!/usr/bin/env bash
# Обновление до новой версии без вопросов — просто вызывает установщик
# с ключом --update (настройки берутся из текущего /root/sprosinab/.env).
#
#     bash /tmp/nab_new/sprosinab/tools/update.sh
#  или короче:
#     nab update
exec bash "$(cd "$(dirname "$0")/.." && pwd)/install.sh" --update "$@"
