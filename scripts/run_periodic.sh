#!/usr/bin/env bash
# run_periodic.sh — launcher periodic job entry point.
# Invokes opencode_skill submit with the weekly scrape prompt.
# This script is called by Process Launcher's periodic_jobs config.
#
# Usage:
#   ./scripts/run_periodic.sh           # normal weekly run (silent on success)
#   ORDS_DRY_RUN=1 ./scripts/run_periodic.sh  # dry run (always sends email)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPENCODE_SKILL_DIR="/Users/grapeot/co/knowledge_working/adhoc_jobs/opencode_skill"
PROMPT_TEMPLATE="${PROJECT_DIR}/prompts/weekly_scrape.md"
TITLE="ORDS Weekly Scrape"

# Render prompt with mode description
RENDERED_PROMPT="/tmp/ords_prompt_$$.md"
trap 'rm -f "${RENDERED_PROMPT}"' EXIT

if [ "${ORDS_DRY_RUN:-0}" = "1" ]; then
  MODE="**当前是 Dry Run 模式。** 这是一次测试运行。无论成功或失败，你都必须发一封邮件报告结果，以便验证邮件通道是否正常。"
  TITLE="ORDS Weekly Scrape (Dry Run)"
else
  MODE="**当前是正常每周定时任务模式。** 成功时不发邮件，失败时调查、修复并发邮件。"
fi

sed "s|{{MODE_DESCRIPTION}}|${MODE}|" "${PROMPT_TEMPLATE}" > "${RENDERED_PROMPT}"

cd "${OPENCODE_SKILL_DIR}"

exec "${OPENCODE_SKILL_DIR}/.venv/bin/python" -m opencode_skill \
  submit \
  --prompt-file "${RENDERED_PROMPT}" \
  --title "${TITLE}" \
  --send-timeout 5