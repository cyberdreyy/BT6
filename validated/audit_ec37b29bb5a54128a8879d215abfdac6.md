## Analysis

Confirmed root cause: `MultiSigContract::confirm` (`multisig/src/lib.rs:246-266`) checks only `!confirmations.contains(&env::signer_account_pk())` and increments the confirmation set/count, while `execute_request`'s `DeleteKey` branch (`multisig/src/lib.rs:198-216`) only purges `requests`/`confirmations` for requests whose `signer_pk == pk` (the request *creator*), and only decrements `num_requests_pk` for that same pk. It never scans `self.confirmations` for entries where the deleted `pk` appears as a *confirmer* of a different, still-open request created by someone else. This is directly analogous to the ENS finding's core defect: a stale/implicit trust record (an ETH2LD marked wrapped/owned) is not re-validated against the live authoritative state (whether the name is actually still wrapped) before being relied upon for a privileged action — here, a confirmation recorded by a since-revoked key is not re-validated against the live member set before being counted toward `num_confirmations`.

### Title
Stale confirmations from deleted multisig keys are still counted toward the confirmation threshold, allowing requests to execute below the live K-of-N quorum - (File: `multisig/src/lib.rs`)

### Summary
`DeleteKey` only removes the deleted key's *own* pending requests and its `num_requests_pk` counter; it does not remove that key's confirmations recorded on other, still-pending requests. `confirm()` then counts `confirmations.len()` (a stale set that may include now-deleted keys) against `num_confirmations` without re-validating that every entry in the set still corresponds to a currently-authorized key.

### Finding Description
`execute_request`'s `DeleteKey` action ( [1](#0-0) ) removes only requests where `r.signer_pk == pk` and clears `num_requests_pk` for that key, then calls `promise.delete_key(pk)`. It does not iterate `self.confirmations` to strip `pk` from confirmation sets belonging to requests created by *other* keys.

`confirm()` ( [2](#0-1) ) trusts `confirmations.len()` as the count of live confirming members: `if confirmations.len() as u32 + 1 >= self.num_confirmations { ...execute... }`. There is no check that each public key stored in `confirmations` is still an active access key on the account.

Sequence:
1. Contract initialized with `num_confirmations = 3`, keys `K1, K2, K3`.
2. `K1` creates request `R` (`add_request`), `K2` confirms `R` (`confirm`) → `confirmations[R] = {K2}` (len 1, below threshold, so it's just recorded).
3. Separately, `K1`/`K2`/`K3` together execute a `DeleteKey{public_key: K2}` request (a legitimate governance action, e.g., because `K2`'s holder is offboarded). This deletes `K2`'s access key and purges only requests where `signer_pk == K2` — `R` (created by `K1`) is untouched, and `confirmations[R]` still contains `K2`.
4. Now the account effectively has only `K1` and `K3` as valid signers (2 total), but `num_confirmations` is still 3.
5. `K3` calls `confirm(R)`. `confirmations.len()` is `1` (the stale `K2` entry) `+ 1` (K3, added) `= 2`, still `< 3`, so it doesn't execute yet — but the *set* now permanently contains a phantom `K2` confirmation. If `num_confirmations` was 2 (or set to 2 via governance while only 2 real keys remain), `K3`'s confirmation alone (with the phantom `K2` already in the set) reaches the threshold and `execute_request(R)` runs — a `Transfer`, `AddKey`, `FunctionCall`, etc., authorized with only **one** currently-live key's real intent, not the required K distinct live confirmers.

This breaks the equality that should hold: `count_of_confirmations_used_for_threshold == count_of_confirmations_from_currently_authorized_members`. The left side can exceed the right side by including confirmations from keys already revoked, exactly as the ENS report's implicit/stale-trust-state was exploited without hitting the explicit "is wrapped" check.

### Impact Explanation
Any request (fund `Transfer`, `AddKey` granting full access, `FunctionCall` to move NEP-141/wNEAR, contract upgrade via `DeployContract`) can execute with a real quorum smaller than the configured `num_confirmations`, because a stale confirmation from a deleted key silently persists and counts toward the threshold. This is an authorization-threshold binding crossed with attacker-controllable timing (the attacker/insider only needs to be one of the surviving keys and wait for or engineer a `DeleteKey` action against a co-signer whose confirmation is already recorded on a pending request) — this can move NEAR out of the multisig account with fewer real approvals than intended, i.e., "a multisig request executed below threshold" (explicitly listed as Critical impact in the rules).

### Likelihood Explanation
Requires only unprivileged use of existing, documented multisig flows: creating requests, confirming, and legitimately deleting a key (a normal key-rotation/offboarding operation any K-of-N multisig will perform over its lifetime). No foundation, redeploy, or out-of-scope actor is needed — any of the existing N members can trigger this by timing a `DeleteKey` request around outstanding unconfirmed requests, which is realistic in normal multisig operation and does not require compromising a victim key or social engineering beyond acting within the multisig's own authorized member set.

### Recommendation
In the `DeleteKey` (and `multisig2`'s `DeleteMember`) branch of `execute_request`, iterate over all entries in `self.confirmations` (not just requests created by the deleted key) and remove the deleted `pk`/member from every confirmation set. Alternatively/additionally, in `confirm()`, before comparing `confirmations.len()` against `num_confirmations`, filter the confirmation set to only currently-valid keys/members (this requires exposing live-access-key/member info) or re-derive the count lazily rather than trusting the persisted, potentially-stale set.

### Proof of Concept
Conceptual reproduction (Rust unit test style, analogous to existing `test_multi_3_of_n` at [3](#0-2) ):
1. `MultiSigContract::new(3)` with keys `K1, K2, K3`.
2. As `K1`: `add_request(R)` (transfer to attacker-controlled receiver).
3. As `K2`: `confirm(R)` → `confirmations[R] = {K2}`.
4. As `K1`+`K2`+`K3` (3 confirmations): execute a separate request `DeleteKey{public_key: K2}` — deletes `K2`'s on-chain access key; per `execute_request` ( [1](#0-0) ), only requests created by `K2` and its `num_requests_pk` entry are cleared — `confirmations[R]` is untouched.
5. Governance sets `num_confirmations = 2` via `SetNumConfirmations` (a normal downsizing operation after removing a key), executed with the remaining 2 live keys `K1, K3`.
6. As `K3`: `confirm(R)` → `confirmations[R].len() == 1 (stale K2) + 1 (K3) == 2 >= num_confirmations(2)` → `execute_request(R)` runs the transfer, moving funds with only one genuinely live confirmer (`K3`) plus a phantom, revoked `K2` confirmation — never a real 2-of-2 approval. [1](#0-0) [2](#0-1)

### Citations

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig/src/lib.rs (L491-531)
```rust
    #[test]
    fn test_multi_3_of_n() {
        let amount = 1_000;
        testing_env!(context_with_key(
            Base58PublicKey::try_from("Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy")
                .unwrap()
                .into(),
            amount
        ));
        let mut c = MultiSigContract::new(3);
        let request = MultiSigRequest {
            receiver_id: bob(),
            actions: vec![MultiSigRequestAction::Transfer {
                amount: amount.into(),
            }],
        };
        let request_id = c.add_request(request.clone());
        assert_eq!(c.get_request(request_id), request);
        assert_eq!(c.list_request_ids(), vec![request_id]);
        c.confirm(request_id);
        assert_eq!(c.requests.len(), 1);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);
        testing_env!(context_with_key(
            Base58PublicKey::try_from("HghiythFFPjVXwc9BLNi8uqFmfQc1DWFrJQ4nE6ANo7R")
                .unwrap()
                .into(),
            amount
        ));
        c.confirm(request_id);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 2);
        assert_eq!(c.get_confirmations(request_id).len(), 2);
        testing_env!(context_with_key(
            Base58PublicKey::try_from("2EfbwnQHPBWQKbNczLiVznFghh9qs716QT71zN6L1D95")
                .unwrap()
                .into(),
            amount
        ));
        c.confirm(request_id);
        // TODO: confirm that funds were transferred out via promise.
        assert_eq!(c.requests.len(), 0);
    }
```
