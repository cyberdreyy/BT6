# Q3772: mfa gate skipped for TEE wallets in MfaApi.ts

## Question
Unified (privy-v2) wallets route through the wallet-api instead of invokeWithMfa; can an attacker convert or select a wallet so MfaApi.verifyMfa's operation avoids the MFA path entirely?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Compare gate coverage for a unified wallet versus an on-device wallet.
- Invariant to test: Both custody paths must enforce equivalent user-approval gates.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: run MfaApi.verifyMfa against both wallet types and assert both require MFA when configured.
