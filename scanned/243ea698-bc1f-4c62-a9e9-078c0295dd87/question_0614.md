# Q0614: raw bytes bypass canonicalisation in rpc.ts

## Question
generateAuthorizationSignature base64-encodes a Uint8Array payload directly instead of canonicalising; can an attacker reach rpc(): builds {version:1 with raw bytes that decode to an envelope for a different operation?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Pass a byte array that is the encoding of another operation's envelope.
- Invariant to test: Raw-byte signing must be domain-separated from envelope signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass envelope bytes as a Uint8Array to rpc(): builds {version:1 and assert domain separation.
