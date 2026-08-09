#!/usr/bin/env bash
# Interactive REPL that drives the real SMS webhook on localhost:8000.
#
# Each turn:
#   1. You type a message.
#   2. The script POSTs a Twilio-shaped form payload to the webhook.
#   3. It then runs `manage.py sms_chat_show` to print the assistant turn(s).
#
# Make sure the dev server is running with DEBUG_SMS=True so outbound SMS
# does NOT actually reach a customer:
#
#   DEBUG_SMS=True OPENAI_API_KEY=sk-... python manage.py runserver 0.0.0.0:8000
#
# Then in another terminal:
#
#   ./scripts/sms_chat.sh                       # default phone
#   ./scripts/sms_chat.sh +15551234567          # custom phone
#   PHONE=+15551234567 ./scripts/sms_chat.sh    # via env var
#   HOST=http://127.0.0.1:8000 ./scripts/sms_chat.sh
#
# Slash commands inside the REPL:
#   /quit          exit
#   /state         show thread state via manage.py
#   /reset         delete this phone's threads + messages
#
# Requirements: bash, curl, python3 with the project's venv active (so
# `python manage.py ...` works).

set -euo pipefail

PHONE="${1:-${PHONE:-+15551234567}}"
HOST="${HOST:-http://localhost:8000}"
TO_NUMBER="${TWILIO_PHONE_NUMBER:-+17245768867}"
WEBHOOK_PATH="${WEBHOOK_PATH:-/api/v1/intake/webhooks/twilio/sms/}"
URL="${HOST}${WEBHOOK_PATH}"

# Resolve manage.py path regardless of cwd
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
MANAGE="python ${PROJECT_DIR}/manage.py"

# ANSI colors (auto-disabled if not a TTY)
if [[ -t 1 ]]; then
    C_USER="\033[94m"
    C_ASSIST="\033[92m"
    C_TOOL="\033[90m"
    C_INFO="\033[96m"
    C_DIM="\033[2m"
    C_BOLD="\033[1m"
    C_ERR="\033[91m"
    C_RESET="\033[0m"
else
    C_USER=""; C_ASSIST=""; C_TOOL=""; C_INFO=""
    C_DIM=""; C_BOLD=""; C_ERR=""; C_RESET=""
fi

LAST_SEQ=-1
SID_COUNTER=0

banner() {
    printf "${C_BOLD}SMS chat REPL via HTTP${C_RESET}\n"
    printf "  url:   %s\n" "$URL"
    printf "  from:  %s   to: %s\n" "$PHONE" "$TO_NUMBER"
    printf "  start the server with DEBUG_SMS=True so outbound SMS is not really sent.\n"
    printf "  type your message, or /quit, /state, /reset.\n\n"
}

post_sms() {
    local body="$1"
    SID_COUNTER=$((SID_COUNTER + 1))
    # Unique across REPL sessions: time-based + counter so the agent's
    # MessageSid dedup never confuses a fresh SMS for a Twilio retry.
    local sid
    sid="SMrepl$(date +%s)${SID_COUNTER}"
    # Twilio form-encodes the webhook payload.
    curl -sS -o /tmp/sms_repl_body.$$ -w '%{http_code}' \
        -X POST "$URL" \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        --data-urlencode "MessageSid=${sid}" \
        --data-urlencode "From=${PHONE}" \
        --data-urlencode "To=${TO_NUMBER}" \
        --data-urlencode "Body=${body}"
}

show_new_messages() {
    local out err tmp
    tmp="/tmp/sms_repl_show.$$"
    out=$($MANAGE sms_chat_show --phone "$PHONE" --since-sequence "$LAST_SEQ" 2>"$tmp" || true)
    err=$(cat "$tmp" 2>/dev/null)
    rm -f "$tmp"
    if [[ -n "$err" ]]; then
        printf "${C_ERR}sms_chat_show stderr:${C_RESET}\n%s\n" "$err"
    fi
    if [[ -z "$out" ]]; then
        printf "${C_DIM}(no new messages — db empty for this phone or query failed)${C_RESET}\n"
        return
    fi
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local seq role content tool_calls
        seq=$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d["sequence"])')
        role=$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d["role"])')
        case "$role" in
            user)
                # Already echoed at input time, skip.
                ;;
            assistant)
                content=$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("content") or "")')
                tool_calls=$(printf '%s' "$line" | python3 -c '
import json,sys
d = json.loads(sys.stdin.read())
out = []
for tc in d.get("tool_calls") or []:
    name = tc.get("name","")
    args = tc.get("arguments",{})
    out.append(f"{name}({json.dumps(args, ensure_ascii=False)})")
print(" | ".join(out))')
                if [[ -n "$tool_calls" ]]; then
                    printf "${C_ASSIST}assistant ›${C_RESET} %s\n" "$tool_calls"
                fi
                if [[ -n "$content" ]]; then
                    printf "${C_ASSIST}assistant ›${C_RESET} content: %s\n" "$content"
                fi
                ;;
            tool)
                local payload tool_name
                tool_name=$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("tool_name",""))')
                payload=$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("content",""))')
                printf "${C_TOOL}tool_result %s: %s${C_RESET}\n" "$tool_name" "$payload"
                ;;
        esac
        LAST_SEQ=$seq
    done <<< "$out"
}

cmd_state() {
    $MANAGE shell -c "
from intake.models import ConversationThread, Contact, JobRecord
from intake.services.sms_chat.utils import normalize_phone
phone = normalize_phone('$PHONE')
threads = list(ConversationThread.objects.filter(phone=phone).order_by('-created_at')[:5])
contact = Contact.objects.filter(phone=phone).order_by('-created_at').first()
print(f'phone={phone}')
if contact:
    print(f'contact={contact.name or \"(unknown)\"} <{contact.email or \"\"}> id={str(contact.id)[:8]}')
    jobs = JobRecord.objects.filter(contact=contact).order_by('-created_at')[:5]
    for j in jobs:
        print(f'  job #{str(j.id)[:8]} {j.service_type or \"?\"} site={j.site or \"?\"} status={j.status}')
else:
    print('contact: (none)')
for t in threads:
    print(f'thread {str(t.id)[:8]} state={t.state} active={t.is_active} msgs={t.messages.count()}')
"
}

cmd_reset() {
    $MANAGE shell -c "
from intake.models import ConversationThread, ConversationMessage
from intake.services.sms_chat.utils import normalize_phone
phone = normalize_phone('$PHONE')
threads = list(ConversationThread.objects.filter(phone=phone).values_list('id', flat=True))
ConversationMessage.objects.filter(thread_id__in=threads).delete()
ConversationThread.objects.filter(id__in=threads).delete()
print(f'reset: deleted {len(threads)} thread(s) for {phone}')
"
    LAST_SEQ=-1
}

# ── Main loop ───────────────────────────────────────────────────────────────
banner

while true; do
    printf "${C_USER}you ›${C_RESET} "
    if ! IFS= read -r BODY; then
        printf "\n${C_DIM}bye.${C_RESET}\n"
        break
    fi
    [[ -z "$BODY" ]] && continue

    BODY_LC=$(printf '%s' "$BODY" | tr '[:upper:]' '[:lower:]')
    case "$BODY_LC" in
        /quit|/exit|quit|exit)
            printf "${C_DIM}bye.${C_RESET}\n"
            break
            ;;
        /state)
            cmd_state
            continue
            ;;
        /reset)
            cmd_reset
            continue
            ;;
    esac

    HTTP_CODE=$(post_sms "$BODY") || HTTP_CODE="error"
    BODY_FILE="/tmp/sms_repl_body.$$"
    if [[ "$HTTP_CODE" != "200" ]]; then
        printf "${C_ERR}HTTP %s${C_RESET}  body: %s\n" "$HTTP_CODE" "$(cat "$BODY_FILE" 2>/dev/null || true)"
        rm -f "$BODY_FILE"
        continue
    fi
    rm -f "$BODY_FILE"

    show_new_messages
    printf "${C_DIM}(http %s)${C_RESET}\n" "$HTTP_CODE"
done

rm -f /tmp/sms_repl_body.* 2>/dev/null || true
