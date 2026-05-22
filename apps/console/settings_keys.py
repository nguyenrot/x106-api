"""Console settings keys + the model-allowlist for the AI ops assistant.

Settings are stored in the `console_settings` table (key/value strings); read
through `get_setting` / `set_setting` in `apps.console.services.settings`.
"""

from __future__ import annotations


# ─── Setting keys ─────────────────────────────────────────────────────────

SETTING_ENABLED = "console.enabled"
SETTING_SYSTEM_PROMPT = "console.system_prompt"
SETTING_AI_MODEL = "console.ai_model"
SETTING_COMMAND_TIMEOUT_SEC = "console.command_timeout_sec"
SETTING_MAX_AGENT_STEPS = "console.max_agent_steps"
SETTING_DESTROY_PHRASE = "console.destroy_phrase"


# ─── Model allowlist (OpenCode Zen free tier) ────────────────────────────

# Hardcoded — `nemotron-3-super-free` is excluded because NVIDIA logs prompts
# and outputs (see https://opencode.ai/docs/zen). VPS ops commands may contain
# hostnames, paths, log snippets we don't want shipped to a third-party.
ALLOWED_MODELS: tuple[str, ...] = (
    "deepseek-v4-flash-free",
    "big-pickle",
    "qwen-3.6-plus-free",
    "minimax-m2.5-free",
)


# ─── Defaults ─────────────────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """Bạn là trợ lý vận hành (ops) cho VPS X106 của Phạm Kỷ Nguyên.

Hạ tầng:
- Ubuntu 24.04 tại 82.197.69.172, 8GB RAM
- PM2 chạy 6 frontend: vibe-hub(3000), me(3001), journal(3002), admin(3003), art(3004), ledger(3005)
- systemd: x106-api(4000), x106-celery-worker, x106-celery-beat
- MySQL 8 native trên :3306 (db finance_app), Redis localhost :6379
- Code ở /var/www/<app>; logs qua `pm2 logs <name>` hoặc `journalctl -u <service>`

Quy tắc làm việc:
1. Bạn có tool `run_shell(command)` để chạy lệnh trên VPS. MỌI lệnh đều cần user approve thủ công.
2. Khi user hỏi tình trạng hệ thống: ưu tiên chia nhỏ thành nhiều lệnh read-only (systemctl status, pm2 list, df -h, free -h, curl health endpoint) trước khi đề xuất bất kỳ lệnh ghi nào.
3. Mỗi tool call: 1 câu giải thích ngắn vì sao chạy lệnh đó.
4. Sau khi có output: tóm tắt bằng tiếng Việt, nêu rõ trạng thái (OK / cảnh báo / lỗi), gợi ý bước tiếp theo.
5. Lệnh nguy hiểm (rm, systemctl stop/restart, apt remove, chmod 777...): cảnh báo rủi ro rõ ràng trước khi đề xuất.
6. Không bịa output. Nếu chưa chạy được lệnh thì chưa biết.
7. Trả lời bằng tiếng Việt, ngắn gọn, súc tích.
"""

DEFAULTS: dict[str, str] = {
    SETTING_ENABLED: "true",
    SETTING_SYSTEM_PROMPT: DEFAULT_SYSTEM_PROMPT,
    SETTING_AI_MODEL: "deepseek-v4-flash-free",
    SETTING_COMMAND_TIMEOUT_SEC: "30",
    SETTING_MAX_AGENT_STEPS: "8",
    SETTING_DESTROY_PHRASE: "DESTROY",
}
