# Q3883: recovery access token reused across providers in MfaSmsApi.ts

## Question
google-drive and icloud recovery both accept recoveryAccessToken; can an attacker present a token from one provider in the other's branch through MfaSmsApi.sendCode?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call recovery with a mismatched provider/token pair.
- Invariant to test: Recovery tokens must be validated against the provider the wallet is bound to.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross provider and token in MfaSmsApi.sendCode and assert rejection.
