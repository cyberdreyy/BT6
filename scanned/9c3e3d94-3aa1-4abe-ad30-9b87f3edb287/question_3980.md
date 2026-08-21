# Q3980: saved-users JSON parsed leniently in LocalStorage.ts

## Question
getSavedUserIds JSON.parses the stored list and filters to strings, returning [] on failure; can an attacker seed a crafted list so LocalStorage.get (JSON.parse) enumerates or switches to unintended ids?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Write a list containing crafted ids and call the switch path.
- Invariant to test: The saved-user list must be integrity-checked against stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign id and assert LocalStorage.get (JSON.parse) rejects a switch to it.
