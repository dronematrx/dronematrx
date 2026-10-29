#!/bin/sh
# Two ArduCopter SITL instances for testing the hardware bridge without aircraft.
# Instance N listens on TCP 5760 + 10*N (MAVLink, SERIAL0) with SYSID_THISMAV = N+1.
# Started by sim/ardupilot_sitl/start.ps1 inside the simhost image.
set -e
cd /sitl
COUNT=${COUNT:-2}
i=0
while [ "$i" -lt "$COUNT" ]; do
  sysid=$((i + 1))
  mkdir -p "/tmp/sitl$i"
  cat > "/tmp/sitl$i/dmx.parm" <<EOF
SYSID_THISMAV $sysid
FS_GCS_ENABLE 1
FS_THR_ENABLE 1
BATT_FS_LOW_ACT 2
FENCE_ENABLE 1
FENCE_ACTION 1
FENCE_ALT_MAX 40
FENCE_RADIUS 200
RTL_ALT 1500
EOF
  lat=$(awk "BEGIN{printf \"%.6f\", 23.2400 + 0.00005 * $i}")
  (cd "/tmp/sitl$i" && /sitl/bin/arducopter -S --model + --speedup 1 -I"$i" \
      --home "$lat,72.650000,50,0" --defaults "/sitl/bin/copter.parm,/tmp/sitl$i/dmx.parm" > "/tmp/sitl$i/out.log" 2>&1 &)
  i=$((i + 1))
done
sleep 2
tail -f /dev/null
