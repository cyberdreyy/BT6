# Q1429: linked_accounts order is server supplied in phoneNumberUtils.ts

## Question
validatePhoneNumber depends on the order of user.linked_accounts as returned by the API; can an attacker influence that order so a different wallet becomes primary?

## Target
- File/function: [src/utils/phoneNumberUtils.ts](src/utils/phoneNumberUtils.ts) - validatePhoneNumber, toE164 (falls back to stripping separators), lastFourDigits, getPhoneCountryCodeAndNumber (defaults to US/+1)
- Entrypoint: privy.auth.phone.sendCode / loginWithCode input handling
- Attacker controls: the raw phone string, including unicode digits, extensions and country prefixes
- Exploit idea: Return the same accounts in a different order and compare selections.
- Invariant to test: Selection must be order-independent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute the account list and assert validatePhoneNumber returns the same wallet.
