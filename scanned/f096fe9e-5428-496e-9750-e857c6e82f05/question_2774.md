# Q2774: switchActiveUser reloads the wallet iframe mid-operation in UserApi.ts

## Question
UserApi.switchActiveUser calls embedded.reload(), which flushes the shared callback queue; can an attacker use UserApi.get to reject or hijack another in-flight wallet operation?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Start a signature, then switch users and observe the flush rejecting the pending callback.
- Invariant to test: A user switch must not be able to interfere with an unrelated in-flight signing operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a sign request, call UserApi.get, and assert the pending request neither resolves with foreign data nor leaks its callback.
