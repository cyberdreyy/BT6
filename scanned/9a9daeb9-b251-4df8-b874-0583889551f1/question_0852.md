# Q0852: cached token validated only by decoded expiry in index.ts

## Question
getProviderAccessToken parses the stored string with the unverified Token wrapper and only checks expiry; can an attacker place a self-issued JWT under that key so crossApp action barrel: loginWithCrossAppAuth treats it as a valid provider token?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Write a crafted JWT with a distant exp under the storage key and trigger a cross-app action.
- Invariant to test: Cached credentials must be validated for provenance, not merely for expiry.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a crafted JWT and assert crossApp action barrel: loginWithCrossAppAuth refuses to use it.
