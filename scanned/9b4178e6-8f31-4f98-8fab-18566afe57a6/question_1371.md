# Q1371: authenticator_state_enabled cryptographic binding failure

## Question
Can an unprivileged attacker reach `authenticator_state_enabled` with crafted transaction contents, object references, gas settings, request parameters, and sequencing and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::authenticator_state_enabled
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: transaction contents, object references, gas settings, request parameters, and sequencing
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
