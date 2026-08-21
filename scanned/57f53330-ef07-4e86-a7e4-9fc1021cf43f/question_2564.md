# Q2564: recovery timeout window is 120 seconds in MfaPasskeyApi.ts

## Question
The user-owned recovery path resolves on a 120000ms timer with onRecovered; can an attacker call onRecovered without completing recovery so MfaPasskeyApi.generateAuthenticationOptions proceeds as if the wallet were restored?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Invoke the onRecovered callback from app-reachable code and observe the operation continuing.
- Invariant to test: Recovery completion must be proven by the iframe, not by a callback invocation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: invoke onRecovered without a real recovery and assert MfaPasskeyApi.generateAuthenticationOptions still fails.
