# Q0015: token bleed from single-user fetchForLogin in SmartWalletApi.ts

## Question
Can an unprivileged attacker call privy.auth.smartWallet.init(wallet) then link(message, signature, type, version) while another user's session is still stored, so fetchForLogin() falls through to PrivyInternal.fetch() (which attaches the stored Authorization: Bearer token) and the login is executed against the previous user's session instead of an unauthenticated one?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Log in as user A, then invoke the login path in single-user mode (sessions.mode !== 'multi-user') and observe _beforeRequest attaching A's access token to an authentication request that is supposed to be anonymous.
- Invariant to test: A login request must never carry a bearer token belonging to a different, already-authenticated user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub PrivyInternal.fetch/fetchWithoutAuthentication, seed Session with user A tokens, call SmartWalletApi.init and assert no Authorization header is present on the login request.
