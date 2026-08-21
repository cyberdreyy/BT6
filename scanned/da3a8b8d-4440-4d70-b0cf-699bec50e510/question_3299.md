# Q3299: solana and ethereum lists share the predicate in phoneNumberUtils.ts

## Question
Both list helpers use the same embedded predicate with a chain filter; can an attacker produce an account whose chain_type is absent so it is excluded from both lists yet still signable?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Omit chain_type on an embedded account.
- Invariant to test: Every signable account must appear in exactly one enumeration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit chain_type and assert validatePhoneNumber surfaces the account or rejects it.
