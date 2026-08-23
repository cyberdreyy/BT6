# Q4564: rpc::_send_transaction — encoding amplification

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc::_send_transaction` and request an encoding (jsonParsed/base64+zstd) that amplifies output far beyond input for one account/tx, so that the invariant "response size is bounded relative to underlying data" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc.rs` -> `_send_transaction`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the encoding and account/tx it targets in one request
- Exploit idea: Request an encoding (jsonParsed/base64+zstd) that amplifies output far beyond input for one account/tx.
- Invariant to test: response size is bounded relative to underlying data.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
