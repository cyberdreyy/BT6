# Q0879: selection helpers feed entropy derivation in phoneNumberUtils.ts

## Question
The values returned by validatePhoneNumber flow into entropy identity and provider construction; can an attacker influence the selection so signing occurs under a different key than the app displayed?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Trace the selected account into the entropy and provider path.
- Invariant to test: The displayed wallet and the signing wallet must be the same account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: assert the account from validatePhoneNumber equals the account used in the signing request.
