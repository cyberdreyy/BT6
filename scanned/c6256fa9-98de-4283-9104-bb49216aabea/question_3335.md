# Q3335: analytics record recovery details in RecoveryApi.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use RecoveryApi.getRecoveryKeyMaterial to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during RecoveryApi.getRecoveryKeyMaterial and assert no recovery method or address is included.
