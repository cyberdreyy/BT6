# Q3223: set-recovery runs after _load succeeded in MfaSmsApi.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so MfaSmsApi.sendCode rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of MfaSmsApi.sendCode and assert the operation aborts.
