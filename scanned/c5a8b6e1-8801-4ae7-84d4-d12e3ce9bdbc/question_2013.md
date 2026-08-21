# Q2013: icloud configuration drives recovery choice in MfaSmsApi.ts

## Question
RecoveryICloudApi.getICloudConfiguration returns configuration consumed as trusted; can an attacker influence the returned configuration so MfaSmsApi.sendCode performs recovery against an attacker-chosen record?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Return a configuration naming a foreign record name and observe the recovery attempt.
- Invariant to test: Recovery targets must be bound to the authenticated user's own records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign record configuration and assert MfaSmsApi.sendCode refuses to use it.
