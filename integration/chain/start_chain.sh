#!/usr/bin/env bash
# Starts a local 4-validator Hyperledger Besu QBFT network (BFT: tolerates 1 faulty validator).
# RPC: node i on http://127.0.0.1:854(5+i). Chain id 20261, zero gas price (permissioned network).
# Usage: BESU=/path/to/besu ./start_chain.sh        (stop with ./stop_chain.sh)
set -euo pipefail
cd "$(dirname "$0")"
BESU=${BESU:-besu}
if [ ! -d networkFiles ]; then
  "$BESU" operator generate-blockchain-config --config-file=qbftConfigFile.json --to=networkFiles --private-key-file-name=key
fi
mkdir -p data logs
i=0
for k in networkFiles/keys/*; do
  mkdir -p data/node$i
  cp "$k/key" "$k/key.pub" data/node$i/
  i=$((i+1))
done
BOOT_PUB=$(sed 's/^0x//' data/node0/key.pub)
BOOTNODE="enode://${BOOT_PUB}@127.0.0.1:30303"
export BESU_OPTS="${BESU_OPTS:--Xmx700m}"
for i in 0 1 2 3; do
  extra=()
  [ "$i" -ne 0 ] && extra=(--bootnodes="$BOOTNODE")
  nohup "$BESU" --data-path=data/node$i --genesis-file=networkFiles/genesis.json \
    --p2p-port=$((30303+i)) --rpc-http-enabled --rpc-http-port=$((8545+i)) \
    --rpc-http-api=ETH,NET,QBFT,WEB3,TXPOOL --host-allowlist="*" --rpc-http-cors-origins="all" \
    --min-gas-price=0 --sync-min-peers=0 --logging=WARN "${extra[@]}" > logs/node$i.log 2>&1 &
  echo $! > data/node$i.pid
  [ "$i" -eq 0 ] && sleep 8
done
echo "waiting for blocks..."
for t in $(seq 1 60); do
  n=$(curl -s -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' http://127.0.0.1:8545 | python3 -c 'import json,sys;print(int(json.load(sys.stdin)["result"],16))' 2>/dev/null || echo 0)
  if [ "$n" -gt 2 ]; then echo "chain producing blocks (height $n)"; exit 0; fi
  sleep 2
done
echo "chain did not start; see logs/" >&2; exit 1
