# Q2301: country default is US in getUserEmbeddedEthereumWallet.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert an explicit country is required.
