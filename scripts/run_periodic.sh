#!/usr/bin/env bash
# run_periodic.sh — launcher periodic job entry point.
# Invokes opencode_skill submit with the weekly scrape + digest prompt.
# This script is called by Process Launcher's periodic_jobs config.
#
# Usage:
#   ./scripts/run_periodic.sh           # normal weekly run (sends digest email)
#   ORDS_DRY_RUN=1 ./scripts/run_periodic.sh  # dry run (always sends email)
#
# Environment variables (set in launcher.yaml or .env):
#   OPENCODE_SKILL_DIR     — path to opencode_skill project (required)
#   RESEND_SKILL_DIR       — path to resend_email_skill project (notification channel)
#   ORDS_NOTIFY_EMAIL      — email address for notifications (required)
#   ORDS_SEARCH_SKILL_PATH — path to the web-search skill used for enrichment
#   ORDS_WRITING_SKILL_DIR — directory holding the drafting CLI skill and workflow docs
#   ORDS_AGY_BIN           — drafting CLI binary (default: agy)
#
# The skill paths are deployment-specific. They are intentionally configuration,
# not hardcoded, so the public prompt template carries no private layout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPENCODE_SKILL_DIR="${OPENCODE_SKILL_DIR:?OPENCODE_SKILL_DIR is required}"
RESEND_SKILL_DIR="${RESEND_SKILL_DIR:?RESEND_SKILL_DIR is required}"
ORDS_NOTIFY_EMAIL="${ORDS_NOTIFY_EMAIL:?ORDS_NOTIFY_EMAIL is required}"
ORDS_SEARCH_SKILL_PATH="${ORDS_SEARCH_SKILL_PATH:?ORDS_SEARCH_SKILL_PATH is required}"
ORDS_WRITING_SKILL_DIR="${ORDS_WRITING_SKILL_DIR:?ORDS_WRITING_SKILL_DIR is required}"
ORDS_AGY_BIN="${ORDS_AGY_BIN:-agy}"
PROMPT_TEMPLATE="${PROJECT_DIR}/prompts/weekly_scrape.md"
TITLE="ORDS Weekly Scrape"

# Render prompt with mode description
RENDERED_PROMPT="/tmp/ords_prompt_$$.md"
trap 'rm -f "${RENDERED_PROMPT}"' EXIT

if [ "${ORDS_DRY_RUN:-0}" = "1" ]; then
  MODE="**当前是 Dry Run 模式。** 这是一次测试运行。无论成功或失败，你都必须发一封邮件报告结果，以便验证邮件通道是否正常。"
  TITLE="ORDS Weekly Scrape (Dry Run)"
else
  MODE="**当前是正常每周定时任务模式。** 抓取与 digest 成功后照常发 digest 邮件；失败时调查、修复并发失败说明邮件。"
fi

# Guard against sed metacharacters and command substitution in substituted values
# (& re-inserts the match, \ escapes, | collides with the delimiter; $ and
# backtick would execute when the rendered bash examples are run).
for value in "${MODE}" "${RESEND_SKILL_DIR}" "${ORDS_NOTIFY_EMAIL}" \
             "${ORDS_SEARCH_SKILL_PATH}" "${ORDS_WRITING_SKILL_DIR}" "${ORDS_AGY_BIN}"; do
  case "${value}" in
    *"|"*|*"&"*|*"\\"*|*'$'*|*'`'*|*$'\n'*)
      echo "Rendered prompt values must be free of '|', '&', backslash, '\$', backtick, newline" >&2
      exit 1
      ;;
  esac
done

sed \
  -e "s|{{MODE_DESCRIPTION}}|${MODE}|" \
  -e "s|{{RESEND_SKILL_DIR}}|${RESEND_SKILL_DIR}|g" \
  -e "s|{{ORDS_NOTIFY_EMAIL}}|${ORDS_NOTIFY_EMAIL}|g" \
  -e "s|{{ORDS_SEARCH_SKILL_PATH}}|${ORDS_SEARCH_SKILL_PATH}|g" \
  -e "s|{{ORDS_WRITING_SKILL_DIR}}|${ORDS_WRITING_SKILL_DIR}|g" \
  -e "s|{{ORDS_AGY_BIN}}|${ORDS_AGY_BIN}|g" \
  "${PROMPT_TEMPLATE}" > "${RENDERED_PROMPT}"

cd "${OPENCODE_SKILL_DIR}"

exec "${OPENCODE_SKILL_DIR}/.venv/bin/python" -m opencode_skill \
  submit \
  --prompt-file "${RENDERED_PROMPT}" \
  --title "${TITLE}" \
  --send-timeout 5
