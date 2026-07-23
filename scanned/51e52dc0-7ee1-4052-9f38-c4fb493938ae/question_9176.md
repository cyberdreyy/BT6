# Q9176: try_extract_field_value cryptographic binding failure

## Question
Can an unprivileged attacker reach `try_extract_field_value` with crafted signature bytes, authenticator payloads, digests, object references, and type data and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: crates/sui-types/src/base_types.rs::try_extract_field_value
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: signature bytes, authenticator payloads, digests, object references, and type data
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
