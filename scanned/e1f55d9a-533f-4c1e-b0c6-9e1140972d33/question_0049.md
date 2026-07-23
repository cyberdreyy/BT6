# Q49: default_ed25519_key_pair cryptographic binding failure

## Question
Can an unprivileged attacker reach `default_ed25519_key_pair` with crafted message bytes, proof fields, amount, recipient, nonce, and chain-domain values and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-bridge/src/config.rs::default_ed25519_key_pair
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: message bytes, proof fields, amount, recipient, nonce, and chain-domain values
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
