# Q3775: mfa gate skipped for TEE wallets in RecoveryApi.ts

## Question
Unified (privy-v2) wallets route through the wallet-api instead of invokeWithMfa; can an attacker convert or select a wallet so RecoveryApi.getRecoveryKeyMaterial's operation avoids the MFA path entirely?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Compare gate coverage for a unified wallet versus an on-device wallet.
- Invariant to test: Both custody paths must enforce equivalent user-approval gates.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: run RecoveryApi.getRecoveryKeyMaterial against both wallet types and assert both require MFA when configured.
