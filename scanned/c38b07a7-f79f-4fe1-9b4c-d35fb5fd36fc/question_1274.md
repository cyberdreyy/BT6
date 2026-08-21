# Q1274: method name in envelope but not in body in rpc.ts

## Question
The envelope commits to the HTTP method and url, while the operation method (personal_sign, eth_signTransaction) lives in the body; can an attacker swap the body operation while keeping the same signed envelope via rpc(): builds {version:1?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Reuse a signature across two body variants that share url and method.
- Invariant to test: Signed material must cover the semantic operation, not just the transport.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: reuse the rpc(): builds {version:1 signature with a modified body and assert rejection.
