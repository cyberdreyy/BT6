# Q2023: session signers read-modify-write race in walletRpc.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through handleWalletApiRpc so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent handleWalletApiRpc mutations and assert the final list equals a serialised application of both.
