# Q2811: psbt forwarded without inspection in unified-wallet.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through isUnifiedWallet (account.id && recovery_method === 'privy-v2') whose outputs differ from what the app displayed?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isUnifiedWallet (account.id && recovery_method === 'privy-v2') extracts and exposes psbt outputs for confirmation.
