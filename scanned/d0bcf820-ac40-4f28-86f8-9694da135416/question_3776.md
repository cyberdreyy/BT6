# Q3776: mfa gate skipped for TEE wallets in RecoveryOAuthApi.ts

## Question
Unified (privy-v2) wallets route through the wallet-api instead of invokeWithMfa; can an attacker convert or select a wallet so RecoveryOAuthApi.generateURL's operation avoids the MFA path entirely?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Compare gate coverage for a unified wallet versus an on-device wallet.
- Invariant to test: Both custody paths must enforce equivalent user-approval gates.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: run RecoveryOAuthApi.generateURL against both wallet types and assert both require MFA when configured.
