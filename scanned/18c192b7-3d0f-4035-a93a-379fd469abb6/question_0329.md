# Q0329: null user returns an empty result in phoneNumberUtils.ts

## Question
validatePhoneNumber returns null or [] for a null user; can an attacker exploit that silent empty result so a caller proceeds with an undefined wallet and signs or funds with the wrong account?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Call the selection with a null user during a session gap.
- Invariant to test: Absence of a user must be an explicit error for wallet-selecting callers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call validatePhoneNumber with null and assert callers cannot proceed.
