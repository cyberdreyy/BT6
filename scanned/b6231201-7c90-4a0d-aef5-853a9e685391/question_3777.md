# Q3777: mfa gate skipped for TEE wallets in RecoveryICloudApi.ts

## Question
Unified (privy-v2) wallets route through the wallet-api instead of invokeWithMfa; can an attacker convert or select a wallet so RecoveryICloudApi.init's operation avoids the MFA path entirely?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Compare gate coverage for a unified wallet versus an on-device wallet.
- Invariant to test: Both custody paths must enforce equivalent user-approval gates.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: run RecoveryICloudApi.init against both wallet types and assert both require MFA when configured.
