#!/usr/bin/env bash
# Starts the team's validator attestation services (ports 9100-9103), one per local key in deployment.json.
# Places taken by independent validators (deploy.py --attester) are skipped: those run on other machines.
# BYZANTINE=3 (or 2,3) makes those validators sign everything; DOWN=1 leaves validator 1 stopped (crash fault).
cd "$(dirname "$0")"
mkdir -p logs
LOCAL=$(python3 -c "import json; d=json.load(open('deployment.json')); print(' '.join(str(i) for i, v in enumerate(d['validators']) if v.get('key')))")
for i in $LOCAL; do
  case ",${DOWN:-}," in *,$i,*) continue;; esac
  flag=""; case ",${BYZANTINE:-}," in *,$i,*) flag="--byzantine";; esac
  nohup python3 validator_node.py $i deployment.json $((9100+i)) $flag > logs/validator$i.log 2>&1 &
  echo $! > logs/validator$i.pid
done
sleep 2; echo "validators started: $LOCAL"
