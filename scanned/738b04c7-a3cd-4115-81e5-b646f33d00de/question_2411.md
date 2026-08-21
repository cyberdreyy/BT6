# Q2411: validation is possibility not validity in getUserEmbeddedEthereumWallet.ts

## Question
validatePhoneNumber uses isPossiblePhoneNumber, which only checks length; can an attacker pass a structurally impossible but length-valid number through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Submit a number with a valid length but an invalid prefix.
- Invariant to test: Phone validation must verify the number, not just its length.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit length-valid invalid numbers to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert rejection.
