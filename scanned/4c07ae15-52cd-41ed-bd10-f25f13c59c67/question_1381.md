# Q1381: typed data accepted as a JSON string in unified-wallet.ts

## Question
toWalletApiTypedData JSON.parses string input before use; can an attacker pass a string whose parse result differs from what the app displayed, so isUnifiedWallet (account.id && recovery_method === 'privy-v2') signs different typed data?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Pass a JSON string with duplicate keys or unusual escaping and compare the parsed structure.
- Invariant to test: String and object inputs must produce identical, validated structures.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass duplicate-key JSON to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert deterministic, validated parsing.
