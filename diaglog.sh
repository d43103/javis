#!/bin/bash
# diaglog - OpenClaw diagnostics.jsonl viewer
# Usage: diaglog [-f] [-n N] [-c category] [-s search]

LOG_FILE="$HOME/Library/Logs/OpenClaw/diagnostics.jsonl"

FOLLOW=false
LINES=50
CATEGORY=""
SEARCH=""

usage() {
    cat <<EOF
diaglog - OpenClaw 앱 로그 뷰어

사용법: diaglog [옵션]

옵션:
  -f           실시간 스트림 (tail -f)
  -n N         최근 N줄 출력 (기본: 50)
  -c CATEGORY  카테고리 필터 (예: talk, voicewake, control, mac-node)
  -s TEXT      텍스트 검색
  -h           도움말

카테고리 예시:
  talk          Talk Mode (PTT) 전체
  talk.tts      TTS 요청/응답
  talk.runtime  Talk 런타임
  voicewake     Voice Wake
  control       게이트웨이 연결
  mac-node      Mac 노드 연결

예시:
  diaglog -f                  실시간 전체 로그
  diaglog -f -c talk          Talk Mode만 실시간
  diaglog -n 100              최근 100줄
  diaglog -s "ttsBaseUrl"     ttsBaseUrl 포함 줄 검색
  diaglog -c talk.tts         TTS 로그만
EOF
}

# 파라미터 파싱
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow) FOLLOW=true; shift ;;
        -n|--lines)  LINES="$2"; shift 2 ;;
        -c|--category) CATEGORY="$2"; shift 2 ;;
        -s|--search)   SEARCH="$2"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "알 수 없는 옵션: $1"; usage; exit 1 ;;
    esac
done

if [[ ! -f "$LOG_FILE" ]]; then
    echo "로그 파일 없음: $LOG_FILE"
    exit 1
fi

# 색상
C_RESET='\033[0m'
C_TIME='\033[0;36m'    # cyan - timestamp
C_CAT='\033[0;33m'     # yellow - category
C_MSG='\033[0;37m'     # white - message
C_ERR='\033[0;31m'     # red - errors
C_TTS='\033[0;32m'     # green - tts
C_WAKE='\033[0;35m'    # magenta - voicewake

PYTHON_SCRIPT='
import sys, json

category_filter = sys.argv[1] if len(sys.argv) > 1 else ""
search_filter   = sys.argv[2] if len(sys.argv) > 2 else ""

RESET = "\033[0m"
C_TIME = "\033[0;36m"
C_CAT  = "\033[0;33m"
C_MSG  = "\033[0;37m"
C_ERR  = "\033[0;31m"
C_TTS  = "\033[0;32m"
C_WAKE = "\033[0;35m"
C_CTRL = "\033[0;34m"

def color_for(cat, level):
    if level in ("error", "fault"):
        return C_ERR
    if cat.startswith("talk.tts"):
        return C_TTS
    if cat.startswith("voicewake"):
        return C_WAKE
    if cat.startswith("control") or cat.startswith("mac-node"):
        return C_CTRL
    return C_MSG

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        ts  = d.get("ts", "")[11:19]
        cat = d.get("category", "")
        msg = d.get("event", d.get("message", ""))
        lvl = d.get("fields", {}).get("level", "info")

        if not msg:
            continue
        if category_filter and category_filter not in cat:
            continue
        if search_filter and search_filter.lower() not in msg.lower():
            continue

        c = color_for(cat, lvl)
        print(f"{C_TIME}{ts}{RESET} {C_CAT}[{cat}]{RESET} {c}{msg}{RESET}", flush=True)
    except Exception:
        pass
'

if [[ "$FOLLOW" == true ]]; then
    echo -e "📋 실시간 로그 스트림 (Ctrl+C로 종료)"
    [[ -n "$CATEGORY" ]] && echo -e "   카테고리 필터: $CATEGORY"
    [[ -n "$SEARCH"   ]] && echo -e "   검색: $SEARCH"
    echo ""
    tail -f "$LOG_FILE" | python3 -c "$PYTHON_SCRIPT" "$CATEGORY" "$SEARCH"
else
    echo -e "📋 최근 ${LINES}줄"
    [[ -n "$CATEGORY" ]] && echo -e "   카테고리 필터: $CATEGORY"
    [[ -n "$SEARCH"   ]] && echo -e "   검색: $SEARCH"
    echo ""
    tail -n "$((LINES * 3))" "$LOG_FILE" | python3 -c "$PYTHON_SCRIPT" "$CATEGORY" "$SEARCH" | tail -n "$LINES"
fi
