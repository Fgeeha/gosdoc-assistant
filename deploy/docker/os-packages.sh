#!/bin/sh
# Установка пакетов ОС для Astra Linux (apt) и РЕД ОС (dnf) по логическим группам.
# Если пакета нет в репозитории ОС, сборка падает с явным сообщением (ADR-0007).
# Использование: os-packages.sh python | worker | node | nginx
set -eu

if command -v apt-get >/dev/null 2>&1; then
    PM=apt
elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
else
    echo "os-packages.sh: не найден apt-get или dnf" >&2
    exit 1
fi

pkgs() {
    case "$PM:$1" in
        apt:python) echo "python3 python3-venv ca-certificates" ;;
        dnf:python) echo "python3 ca-certificates shadow-utils" ;;
        apt:worker) echo "tesseract-ocr tesseract-ocr-rus libreoffice-writer fonts-liberation2" ;;
        dnf:worker) echo "tesseract tesseract-langpack-rus libreoffice-writer libreoffice-headless liberation-fonts" ;;
        apt:node)   echo "nodejs npm" ;;
        dnf:node)   echo "nodejs npm" ;;
        apt:nginx)  echo "nginx" ;;
        dnf:nginx)  echo "nginx" ;;
        *) echo "os-packages.sh: неизвестная группа $1" >&2; exit 1 ;;
    esac
}

missing() {
    echo "ОШИБКА: пакета '$1' нет в репозиториях ОС базового образа." >&2
    echo "Базовый образ не подменяется автоматически, см. docs/adr/0007-base-images.md" >&2
    exit 1
}

# Образ уже с Python 3.11+ (python:3.11-slim в dev) — второй интерпретатор не ставим.
if [ "$1" = python ] && python3 -c 'import sys, venv; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    exit 0
fi

LIST=$(pkgs "$1")
if [ "$PM" = apt ]; then
    apt-get update
    for p in $LIST; do
        apt-cache show "$p" >/dev/null 2>&1 || missing "$p"
    done
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $LIST
    apt-get clean
    rm -rf /var/lib/apt/lists/*
else
    for p in $LIST; do
        dnf -q list "$p" >/dev/null 2>&1 || missing "$p"
    done
    # shellcheck disable=SC2086
    dnf install -y --setopt=install_weak_deps=False $LIST
    dnf clean all
fi

if [ "$1" = python ]; then
    python3 -c 'import sys; assert sys.version_info >= (3, 11), f"Нужен Python 3.11+, в ОС {sys.version}"'
fi
