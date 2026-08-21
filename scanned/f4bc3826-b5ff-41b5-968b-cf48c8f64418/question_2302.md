# Q2302: country default is US in getAllUserEmbeddedEthereumWallets.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert an explicit country is required.
