# Q2969: no ownership assertion in the helper in phoneNumberUtils.ts

## Question
validatePhoneNumber filters the supplied user object without asserting the object came from an authenticated read; can an attacker pass a fabricated user so the helper returns an account they control?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Pass a hand-built user object.
- Invariant to test: Helpers that select signing accounts must require server-confirmed input.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a fabricated user to validatePhoneNumber and assert the caller re-validates.
