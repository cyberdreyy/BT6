# Q1051: access list normalisation drops entries in unified-wallet.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through isUnifiedWallet (account.id && recovery_method === 'privy-v2') that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through isUnifiedWallet (account.id && recovery_method === 'privy-v2').
