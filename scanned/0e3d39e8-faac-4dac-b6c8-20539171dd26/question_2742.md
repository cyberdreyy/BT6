# Q2742: array helpers build objects from strings in getAllUserEmbeddedEthereumWallets.ts

## Question
toObjectKeys reduces an array of strings into an object with a constant value; can an attacker supply an entry such as __proto__ through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum so the produced object has a polluted prototype?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass prototype-named entries.
- Invariant to test: Object construction from input arrays must be prototype-safe.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass '__proto__' to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert a null-prototype or filtered result.
