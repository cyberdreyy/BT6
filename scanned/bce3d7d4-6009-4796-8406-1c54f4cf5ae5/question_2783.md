# Q2783: password type check only in MfaSmsApi.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via MfaSmsApi.sendCode that later allows offline recovery?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/client/mfa/MfaSmsApi.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call MfaSmsApi.sendCode with a weak password and assert the configured policy is enforced.
