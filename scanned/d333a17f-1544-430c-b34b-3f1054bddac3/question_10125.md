# Q10125: compute_unchanged_consensus_objects cryptographic binding failure

## Question
Can an unprivileged attacker reach `compute_unchanged_consensus_objects` with crafted shared_objects, loaded_per_epoch_config_objects, changed_objects and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-types/src/effects/effects_v2.rs::compute_unchanged_consensus_objects
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: shared_objects, loaded_per_epoch_config_objects, changed_objects
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
