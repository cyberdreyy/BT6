# Q3409: selection result feeds funding destinations in phoneNumberUtils.ts

## Question
Funding and deposit flows use validatePhoneNumber to choose a destination; can an attacker influence the selection so funds arrive at a wallet the user did not intend?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Change the account set before a funding flow.
- Invariant to test: Funding destinations must be user-confirmed, not derived by a helper.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: change accounts before funding and assert confirmation is re-requested.
