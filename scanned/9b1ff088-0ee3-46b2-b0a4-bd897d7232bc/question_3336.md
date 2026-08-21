# Q3336: analytics record recovery details in RecoveryOAuthApi.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use RecoveryOAuthApi.generateURL to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during RecoveryOAuthApi.generateURL and assert no recovery method or address is included.
