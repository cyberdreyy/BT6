# Q7071: jwk_id_equal cryptographic binding failure

## Question
Can an unprivileged attacker reach `jwk_id_equal` with crafted a, b and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-framework/packages/sui-framework/sources/authenticator_state.move::jwk_id_equal
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: a, b
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
