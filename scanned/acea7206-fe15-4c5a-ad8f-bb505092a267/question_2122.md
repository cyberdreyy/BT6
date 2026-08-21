# Q2122: needs-recovery error type is attacker shaped in MfaApi.ts

## Question
errorIndicatesRecoveryIsNeeded only checks error.type === 'wallet_not_on_device' on a duck-typed object; can an attacker deliver an object with that type so MfaApi.verifyMfa silently starts a recovery flow?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Feed an error-shaped object with the matching type into the embedded error path.
- Invariant to test: Recovery must be triggered only by an authenticated iframe error, not by any object with a matching type field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a plain object {type:'wallet_not_on_device'} to MfaApi.verifyMfa and assert recovery is not initiated.
