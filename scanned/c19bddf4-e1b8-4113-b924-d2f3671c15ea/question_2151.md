# Q2151: options forwarded to the broadcaster in unified-wallet.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through isUnifiedWallet (account.id && recovery_method === 'privy-v2') that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isUnifiedWallet (account.id && recovery_method === 'privy-v2') pins preflight-relevant options.
