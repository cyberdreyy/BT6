# Q0501: unsupported methods fall through to the public RPC in unified-wallet.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use isUnifiedWallet (account.id && recovery_method === 'privy-v2') to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/wallet-api/unified-wallet.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call isUnifiedWallet (account.id && recovery_method === 'privy-v2') with an arbitrary method and assert it is rejected.
