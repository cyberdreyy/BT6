# Q2563: recovery timeout window is 120 seconds in MfaSmsApi.ts

## Question
The user-owned recovery path resolves on a 120000ms timer with onRecovered; can an attacker call onRecovered without completing recovery so MfaSmsApi.sendCode proceeds as if the wallet were restored?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Invoke the onRecovered callback from app-reachable code and observe the operation continuing.
- Invariant to test: Recovery completion must be proven by the iframe, not by a callback invocation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: invoke onRecovered without a real recovery and assert MfaSmsApi.sendCode still fails.
