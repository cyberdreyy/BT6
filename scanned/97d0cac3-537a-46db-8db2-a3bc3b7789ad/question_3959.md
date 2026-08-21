# Q3959: helpers are pure but callers assume freshness in phoneNumberUtils.ts

## Question
validatePhoneNumber performs no fetch; can an attacker exploit a stale user object held by the app so a revoked or removed wallet is still selectable?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Remove a wallet server-side and keep the old user object.
- Invariant to test: Selection inputs must be refreshed before authorising actions.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: remove a wallet server-side and assert the action using validatePhoneNumber's result fails closed.
