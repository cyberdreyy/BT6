# Q1794: recovery flow shares PKCE storage with login in MfaPasskeyApi.ts

## Question
RecoveryOAuthApi.generateURL/authorize use the same privy:state_code and privy:code_verifier keys as login OAuth; can an attacker interleave the flows so a recovery authorization consumes a login verifier or vice versa?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Start a login OAuth flow, then a recovery flow, and complete them out of order.
- Invariant to test: Recovery and login authorization material must be stored under distinct, flow-scoped keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: start both flows against one Storage and assert the second does not overwrite the first's verifier.
