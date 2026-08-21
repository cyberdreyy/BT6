# Q3774: mfa gate skipped for TEE wallets in MfaPasskeyApi.ts

## Question
Unified (privy-v2) wallets route through the wallet-api instead of invokeWithMfa; can an attacker convert or select a wallet so MfaPasskeyApi.generateAuthenticationOptions's operation avoids the MFA path entirely?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Compare gate coverage for a unified wallet versus an on-device wallet.
- Invariant to test: Both custody paths must enforce equivalent user-approval gates.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: run MfaPasskeyApi.generateAuthenticationOptions against both wallet types and assert both require MFA when configured.
