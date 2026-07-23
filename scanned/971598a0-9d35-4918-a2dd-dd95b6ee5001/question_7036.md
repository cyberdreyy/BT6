# Q7036: check_sorted cryptographic binding failure

## Question
Can an unprivileged attacker reach `check_sorted` with crafted new_active_jwks and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-framework/packages/sui-framework/sources/authenticator_state.move::check_sorted
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: new_active_jwks
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
