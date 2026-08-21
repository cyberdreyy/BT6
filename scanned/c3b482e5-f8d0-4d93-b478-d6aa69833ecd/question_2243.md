# Q2243: remove clears every signer in walletRpc.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use handleWalletApiRpc to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call handleWalletApiRpc with multiple signers present and assert only the requested one is removed.
