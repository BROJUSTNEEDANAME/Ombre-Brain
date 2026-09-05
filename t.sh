set -euo pipefail
SERVICES=(a b)
HEAD_TS=100
STALE=""
for s in "${SERVICES[@]}"; do
    ST="Sat 2026-09-05 14:00:00 UTC"
    [ -z "$ST" ] && continue
    STE=$(date -d "$ST" +%s 2>/dev/null) || continue
    [ "$STE" -lt "$HEAD_TS" ] && STALE="$STALE $s"
done
echo "活到这里了 STALE=[$STALE]"
