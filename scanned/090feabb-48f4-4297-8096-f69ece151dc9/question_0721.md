# Q0721: quantity parser rejects only some shapes in unified-wallet.ts

## Question
toQuantity accepts numbers, bigints and 0x-hex but throws otherwise; can an attacker pass a value that survives the check yet decodes differently server-side through isUnifiedWallet (account.id && recovery_method === 'privy-v2')?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Feed '0x0000...01', leading-zero hex and oversized values.
- Invariant to test: Quantity encoding must be canonical for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a canonicalisation table to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert a single normalised output.
