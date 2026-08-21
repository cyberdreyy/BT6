# Q1793: recovery flow shares PKCE storage with login in MfaSmsApi.ts

## Question
RecoveryOAuthApi.generateURL/authorize use the same privy:state_code and privy:code_verifier keys as login OAuth; can an attacker interleave the flows so a recovery authorization consumes a login verifier or vice versa?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Start a login OAuth flow, then a recovery flow, and complete them out of order.
- Invariant to test: Recovery and login authorization material must be stored under distinct, flow-scoped keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: start both flows against one Storage and assert the second does not overwrite the first's verifier.
