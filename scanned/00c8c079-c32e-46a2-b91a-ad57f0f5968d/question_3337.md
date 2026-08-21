# Q3337: analytics record recovery details in RecoveryICloudApi.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use RecoveryICloudApi.init to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during RecoveryICloudApi.init and assert no recovery method or address is included.
