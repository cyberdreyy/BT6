# Q15661: no_additional_script_signature_checks cryptographic binding failure

## Question
Can an unprivileged attacker reach `no_additional_script_signature_checks` with crafted package bytes, module metadata, type layouts, dependencies, and upgrade parameters and make a signature, authenticator, proof, address binding, or message digest verify for a different intent than the one the signer or protocol actually authorized?

## Target
- File/function: external-crates/move/crates/move-bytecode-verifier/src/script_signature.rs::no_additional_script_signature_checks
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: package bytes, module metadata, type layouts, dependencies, and upgrade parameters
- Exploit idea: Probe domain separation, address derivation, intent hashing, bitmap binding, and proof-context checks for alternate-valid encodings.
- Invariant to test: Every accepted cryptographic proof must bind exactly one signer set, one intent, one domain, and one resulting state transition.
- Expected Immunefi impact: Critical — unauthorized transaction, object access, or fund movement via signature or proof confusion.
- Fast validation: Start from a valid local signature or proof, mutate a single bound field, and test whether verification still passes for a different action.
