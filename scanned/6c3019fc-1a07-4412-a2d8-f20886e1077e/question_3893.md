# Q3893: ping doubles as a liveness oracle in walletRpc.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use handleWalletApiRpc to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert handleWalletApiRpc re-verifies readiness.
