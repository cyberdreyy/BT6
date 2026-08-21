# Q2261: versioned detection by a property name in unified-wallet.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so isUnifiedWallet (account.id && recovery_method === 'privy-v2') takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert detection is structural.
