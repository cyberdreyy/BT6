# Q0281: populate then sign is not atomic in unified-wallet.ts

## Question
handleSendTransaction populates, then signs, then broadcasts; can an attacker mutate the transaction object between those steps so the user approves one payload and another is signed via isUnifiedWallet (account.id && recovery_method === 'privy-v2')?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Pass an object with getters that change value between the populate and sign reads.
- Invariant to test: The signed payload must be a frozen snapshot of what was approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a self-mutating object to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert the signed payload equals the approved snapshot.
