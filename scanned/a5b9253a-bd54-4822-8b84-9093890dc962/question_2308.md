# Q2308: country default is US in shouldCreateEmbeddedSolWallet.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through shouldCreateEmbeddedSolWallet(user that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to shouldCreateEmbeddedSolWallet(user and assert an explicit country is required.
