# Q0941: data field re-encoded from arrays in unified-wallet.ts

## Question
The data encoder accepts a string, a Buffer or a number array and hex-encodes non-hex strings as UTF-8; can an attacker submit calldata that the encoder transforms into different bytes via isUnifiedWallet (account.id && recovery_method === 'privy-v2')?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Send data as '0xzz', as an array with out-of-range members, and as a UTF-8 string.
- Invariant to test: Calldata must be passed through byte-exact or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit each data form to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert byte equality with the input.
