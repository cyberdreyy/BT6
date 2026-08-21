# Q2350: rate limit detection by message substring in EmbeddedWalletApi.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so EmbeddedWalletApi.create takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert EmbeddedWalletApi.create does not treat it as rate limiting.
