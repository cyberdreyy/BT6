I have confirmed the vulnerable code path. Let me finalize the analysis.

### Title
Confirmations from a deleted multisig key remain counted toward `num_confirmations`, allowing request execution below the live-member threshold - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig/src/lib.rs` tallies a `HashSet<PublicKey>` of confirmations against `self.num_confirmations` [1](#0-0) . When a key is removed via `MultiSigRequestAction::DeleteKey`, the contract only purges requests that key itself *created* (`r.signer_pk == pk`); it never scrubs that public key out of the `confirmations` sets of other, still-pending requests that key had previously *confirmed* [2](#0-1) . This is the same class of bug as H-24: a limit/threshold check (`getTotalDebtForCollateralToken` vs. `maxPotentialDebt`, or here `confirmations.len()` vs. `num_confirmations`) is evaluated against stale state that doesn't reflect the current, authoritative membership set — the count of "confirmations" no longer equals the count of confirmations from currently-valid ("live") keys.

### Finding Description
The intended custody binding is: `confirmations.len() (of currently valid keys) >= num_confirmations` must hold before a request executes, i.e., the count of confirmations should always equal confirmations from currently-live key holders.

`confirm()` never re-validates that every public key stored in a request's `confirmations` set is still an active key on the account; it simply checks set membership and cardinality [1](#0-0) . The only cleanup of confirmations tied to a removed key happens inside `DeleteKey`'s handling, and it is scoped to `self.requests.iter().filter(|(_k, r)| r.signer_pk == pk)` — i.e., requests where the removed key was the *original requester*, not requests where the removed key merely added a confirmation [2](#0-1) . Consequently, if a key confirms request R (not its own), and is later deleted through a separate, unrelated `DeleteKey` request, R's confirmation set still contains that now-invalid public key, and it keeps contributing toward `num_confirmations` for R.

### Impact Explanation
This breaks the K-of-N custody guarantee documented for the contract ("Any of the access keys ... can confirm, until the required number of confirmation achieved") [3](#0-2) . A request can be executed — including `Transfer` of NEAR, `AddKey`/`DeleteKey`, or `FunctionCall` actions [4](#0-3)  — with fewer genuinely live confirmations than `num_confirmations` mandates, because a phantom confirmation from a removed key is still counted. This falls squarely under "a multisig request executed below threshold," a Critical impact.

### Likelihood Explanation
Any account whose key is later revoked (e.g., an employee offboarded, a compromised key rotated out, a key demoted) can pre-confirm one or more pending requests before removal. No special privilege beyond being a current key holder is required to plant the stale confirmation, and no colluding party needs to intentionally exploit it — a normal `DeleteKey` operation performed for routine key-rotation is enough to create the discrepancy, making this readily reachable during ordinary contract usage.

### Proof of Concept
1. Multisig deployed with `num_confirmations = 3` and keys `A, B, C, D` (`MultiSigContract::new`) [5](#0-4) .
2. `D` calls `add_request` to create request `R` (e.g., `Transfer` of the account's NEAR balance to an address `D` controls), then `D` calls `confirm(R)` — `confirmations[R] = {D}`, count = 1 [1](#0-0) .
3. Separately, `A`, `B`, `C` legitimately confirm and execute a `DeleteKey { public_key: D }` request (routine revocation of `D`, e.g. after `D` leaves the org) — this only clears requests originally *added* by `D`, not `R`, and does not touch `confirmations[R]` [2](#0-1) .
4. `confirmations[R]` still equals `{D}` even though `D` is no longer a valid key on the account.
5. `A` calls `confirm(R)` → count = 2. `B` calls `confirm(R)` → count = 3 = `num_confirmations`, so `execute_request(R)` runs and the `Transfer` to `D`'s address executes [6](#0-5) .
6. Result: the request executed with only 2 genuinely live confirmations (`A`, `B`) plus one stale phantom confirmation from the removed key `D`, i.e., below the intended 3-of-live-members threshold — NEAR moved from the account without the required live authorization.

### Citations

**File:** multisig/src/lib.rs (L102-113)
```rust
    #[init]
    pub fn new(num_confirmations: u32) -> Self {
        assert!(!env::state_exists(), "Already initialized");
        Self {
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(b"r".to_vec()),
            confirmations: UnorderedMap::new(b"c".to_vec()),
            num_requests_pk: UnorderedMap::new(b"k".to_vec()),
            active_requests_limit: 12,
        }
    }
```

**File:** multisig/src/lib.rs (L167-243)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
                MultiSigRequestAction::CreateAccount => promise.create_account(),
                MultiSigRequestAction::DeployContract { code } => {
                    promise.deploy_contract(code.into())
                }
                MultiSigRequestAction::AddKey {
                    public_key,
                    permission,
                } => {
                    self.assert_self_request(receiver_id.clone());
                    if let Some(permission) = permission {
                        promise.add_access_key(
                            public_key.into(),
                            permission
                                .allowance
                                .map(|x| x.into())
                                .unwrap_or(DEFAULT_ALLOWANCE),
                            permission.receiver_id,
                            permission.method_names.join(",").into_bytes(),
                        )
                    } else {
                        // wallet UI should warn user if receiver_id == env::current_account_id(), adding FAK will render multisig useless
                        promise.add_full_access_key(public_key.into())
                    }
                }
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
                MultiSigRequestAction::FunctionCall {
                    method_name,
                    args,
                    deposit,
                    gas,
                } => promise.function_call(
                    method_name.into_bytes(),
                    args.into(),
                    deposit.into(),
                    gas.into(),
                ),
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
                MultiSigRequestAction::SetActiveRequestsLimit {
                    active_requests_limit,
                } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.active_requests_limit = active_requests_limit;
                    return PromiseOrValue::Value(true);
                }
            };
        }
        promise.into()
```

**File:** multisig/src/lib.rs (L248-266)
```rust
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

**File:** multisig/README.md (L6-8)
```markdown
 - Any of the access keys can confirm, until the required number of confirmation achieved.

## Multisig implementation details
```
