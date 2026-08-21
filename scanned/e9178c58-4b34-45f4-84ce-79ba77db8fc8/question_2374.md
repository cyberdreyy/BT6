# Q2374: wallet-api path compiled from route templates in rpc.ts

## Question
getCompiledPath interpolates wallet_id into the route path before it is signed; can an attacker supply a wallet_id containing path separators so rpc(): builds {version:1 signs and calls a different endpoint?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Pass a wallet id containing '/' or '%2F'.
- Invariant to test: Path parameters must be encoded before compilation and signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a separator-bearing wallet id to rpc(): builds {version:1 and assert encoding or rejection.
