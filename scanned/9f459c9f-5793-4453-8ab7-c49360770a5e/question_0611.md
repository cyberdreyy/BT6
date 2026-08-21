# Q0611: bigint stringification changes values in unified-wallet.ts

## Question
handleSignTransaction converts bigint fields with toHex over Object.keys, including nested call values; can an attacker craft a field whose conversion is lossy so isUnifiedWallet (account.id && recovery_method === 'privy-v2') signs a different value than displayed?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Submit values at the edges of the bigint/number/hex conversions and diff the serialised output.
- Invariant to test: Numeric conversion must be exact and total for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: property-test numeric fields through isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert round-trip equality.
