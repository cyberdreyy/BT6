# Q0666: returned user object is not re-read from session in PasskeyApi.ts

## Question
Does PasskeyApi.generateAuthenticationOptions return the user object from the authenticate response (merged by mergeUser) without re-reading the freshly stored session, letting a stale or attacker-influenced response drive the app's is_new_user and linked_accounts view?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Return an authenticate response whose user payload disagrees with the token subject and observe which value reaches the setUser callback.
- Invariant to test: The user object handed to setUser must be consistent with the subject of the token that was just stored.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the response user.id differ from the JWT sub in the same response and assert PasskeyApi.generateAuthenticationOptions rejects instead of calling setUser.
