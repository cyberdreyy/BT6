# Q2988: embedded_wallet_config.mode changes the key custody path in Token.ts

## Question
EmbeddedWalletApi branches on config.embedded_wallet_config.mode ('user-controlled-server-wallets-only'); can an attacker influence which branch Token.parse takes so a wallet is created under a different custody model than the app intends?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Serve a config with a flipped mode and observe the create path taken.
- Invariant to test: The custody branch must be authenticated and not flip based on a single fetched field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: flip the mode field between two calls and assert Token.parse does not silently change custody path for an existing wallet.
