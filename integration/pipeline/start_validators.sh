#!/usr/bin/env bash
# Starts the 4 validator attestation services (ports 9100-9103).
# BYZANTINE=3 (or 2,3) makes those validators sign everything; DOWN=1 leaves validator 1 stopped (crash fault).
cd "$(dirname "$0")"
mkdir -p logs
for i in 0 1 2 3; do
  case ",${DOWN:-}," in *,$i,*) continue;; esac
  flag=""; case ",${BYZANTINE:-}," in *,$i,*) flag="--byzantine";; esac
  nohup python3 validator_node.py $i deployment.json $((9100+i)) $flag > logs/validator$i.log 2>&1 &
  echo $! > logs/validator$i.pid
done
sleep 2; echo "validators started"
