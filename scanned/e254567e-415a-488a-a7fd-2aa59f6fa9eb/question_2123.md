# Q2123: needs-recovery error type is attacker shaped in MfaSmsApi.ts

## Question
errorIndicatesRecoveryIsNeeded only checks error.type === 'wallet_not_on_device' on a duck-typed object; can an attacker deliver an object with that type so MfaSmsApi.sendCode silently starts a recovery flow?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Feed an error-shaped object with the matching type into the embedded error path.
- Invariant to test: Recovery must be triggered only by an authenticated iframe error, not by any object with a matching type field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a plain object {type:'wallet_not_on_device'} to MfaSmsApi.sendCode and assert recovery is not initiated.
