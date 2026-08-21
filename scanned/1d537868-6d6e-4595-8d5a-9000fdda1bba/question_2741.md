# Q2741: array helpers build objects from strings in getUserEmbeddedEthereumWallet.ts

## Question
toObjectKeys reduces an array of strings into an object with a constant value; can an attacker supply an entry such as __proto__ through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 so the produced object has a polluted prototype?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass prototype-named entries.
- Invariant to test: Object construction from input arrays must be prototype-safe.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass '__proto__' to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert a null-prototype or filtered result.
