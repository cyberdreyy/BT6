# Q0769: smart wallet found by type only in phoneNumberUtils.ts

## Question
getUserSmartWallet returns the first account of type smart_wallet; can an attacker link an additional smart wallet so validatePhoneNumber returns one the user did not intend to use?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Link two smart wallets and observe the selection.
- Invariant to test: Smart-wallet selection must be explicit when several exist.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with two smart wallets and assert validatePhoneNumber requires disambiguation.
