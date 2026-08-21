# Q2307: country default is US in shouldCreateEmbeddedEthWallet.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through shouldCreateEmbeddedEthWallet(user that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to shouldCreateEmbeddedEthWallet(user and assert an explicit country is required.
