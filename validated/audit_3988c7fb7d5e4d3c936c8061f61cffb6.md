### Title
Allow-list check on `options.Build.Image.Name` validates the pre-expansion string while `createVMTunnel` uses the post-expansion string, permitting variable-substitution bypass of `allowed_images` - ([File: executors/internal/autoscaler/acquisition.go])

### Summary
`executors/instance/instance.go` validates the job's literal, unexpanded image name against the runner-configured `allowed_images` glob patterns via `common.VerifyAllowedImage`, but the value actually handed to the nesting daemon in `acquisitionRef.createVMTunnel` is the *variable-expanded* string produced by `options.Build.GetAllVariables().ExpandValue(image)`. A pipeline author who fully controls their own `.gitlab-ci.yml` `variables:` can craft an image string whose literal form matches an allowed glob pattern but whose expanded form resolves to a different value passed to `nc.Create`.

### Finding Description
In `executors/instance/instance.go` `Prepare` (lines 52-66):
```go
if options.Config.Autoscaler.VMIsolation.Enabled && options.Build.Image.Name != "" {
    ...
    if err := common.VerifyAllowedImage(common.VerifyAllowedImageOptions{
        Image: options.Build.Image.Name, // literal, unexpanded string
        ...
    }, e.BuildLogger); err != nil {
        return err
    }
}
``` [1](#0-0) 

This check runs against `options.Build.Image.Name` verbatim, using `doublestar.Match` in `common.VerifyAllowedImage` (`common/allowed_images.go` lines 20-26). [2](#0-1) 

Later, `environment.Prepare` is invoked, which for the autoscaler/instance path routes into `acquisitionRef.createVMTunnel` (`executors/internal/autoscaler/acquisition.go` lines 200-221):
```go
image := nestingCfg.Image
if options.Build.Image.Name != "" && ref.mapJobImageToVMImage {
    image = options.Build.Image.Name
}
image = options.Build.GetAllVariables().ExpandValue(image)
...
vm, stompedVMID, err = nc.Create(ctx, image, &slot)
``` [3](#0-2) 

`mapJobImageToVMImage` is `true` for the instance executor (`executors/instance/instance.go` `NewProvider`, `autoscaler.Config{MapJobImageToVMImage: true}`), so this path is always exercised. [4](#0-3) 

The root cause is a validate-then-use inconsistency: the security decision is made on one string, the security-sensitive action is performed with a different, attacker-influenceable string. Because `doublestar` `*` does not cross `/` boundaries, an attacker can construct `image: "<allowed-prefix>$VAR"` where the literal text (containing only the placeholder `$VAR`, no `/`) matches an admin's glob such as `"<allowed-prefix>*"`, while defining `VAR` in the job's own `variables:` block to expand into arbitrary trailing (or even path-containing) content. `ExpandValue` performs simple substitution; the allow-list is never re-evaluated against the resulting string before it reaches `nc.Create`.

### Impact Explanation
This lets an unprivileged pipeline author, when VM isolation + `MapJobImageToVMImage` are enabled and the runner admin has configured a non-trivial `allowed_images` restriction, cause the runner to request VM creation with an image identifier that diverges from what was actually vetted by the allow-list check. Depending on how the nesting daemon resolves image identifiers (a component outside this repo), this can select a nesting VM image the admin did not intend to permit, undermining the `allowed_images` restriction that is the only isolation control on which VM template a job can request.

### Likelihood Explanation
Preconditions are realistic and fully attacker-reachable without any elevated privilege: VM isolation enabled, `MapJobImageToVMImage: true` (always true for the instance executor), and a restrictive `allowed_images` list with wildcard/prefix patterns (the common configuration style, since exact-match-only lists would be impractical). The attacker only needs to define a `variables:` entry in their own `.gitlab-ci.yml` and set `image:` to reference it - both fully within a pipeline author's control. The bug is deterministic and repeatable.

### Recommendation
Perform the `common.VerifyAllowedImage` check against the *expanded* image string (i.e., after `options.Build.GetAllVariables().ExpandValue`), not the raw literal, and reject the job if the expanded value differs from the literal in a way that changes the match result. Alternatively, disallow variable expansion for the image field entirely for the VM-isolation nesting path, or re-validate the fully-expanded string immediately before calling `nc.Create` in `createVMTunnel`.

### Proof of Concept
Go test sketch for `executors/internal/autoscaler` (or an integration test around `instance.executor.Prepare` + `acquisitionRef.createVMTunnel`):
```go
func TestAllowedImageBypassViaVariableExpansion(t *testing.T) {
    build := &common.Build{
        JobResponse: common.JobResponse{
            Image: common.Image{Name: "allowed-prefix-$INJECT"},
        },
    }
    build.Variables = append(build.Variables, spec.Variable{Key: "INJECT", Value: "-evil-project-image"})

    // Step 1: mimic instance.go's check - expect this to PASS with AllowedImages=["allowed-prefix-*"]
    err := common.VerifyAllowedImage(common.VerifyAllowedImageOptions{
        Image:         build.Image.Name, // literal "allowed-prefix-$INJECT"
        AllowedImages: []string{"allowed-prefix-*"},
    }, logger)
    require.NoError(t, err) // check passes on the literal

    // Step 2: mimic createVMTunnel's computation of the actual image used
    expanded := build.GetAllVariables().ExpandValue(build.Image.Name)

    // Assert the value actually sent to nc.Create differs from what was vetted,
    // demonstrating the check and the use operate on different strings.
    assert.Equal(t, "allowed-prefix-evil-project-image", expanded)
    assert.NotEqual(t, build.Image.Name, expanded)
}
```
Expected assertions: the allow-list check succeeds against the literal `"allowed-prefix-$INJECT"`, but the value ultimately passed to `nc.Create` (`expanded`) is attacker-controlled and was never itself checked against `allowed_images`, proving the validate/use mismatch.

### Citations

**File:** executors/instance/instance.go (L52-66)
```go
	if options.Config.Autoscaler.VMIsolation.Enabled && options.Build.Image.Name != "" {
		var allowed []string
		if options.Config.Instance != nil {
			allowed = options.Config.Instance.AllowedImages
		}

		// verify image is allowed
		if err := common.VerifyAllowedImage(common.VerifyAllowedImageOptions{
			Image:         options.Build.Image.Name,
			OptionName:    "images",
			AllowedImages: allowed,
		}, e.BuildLogger); err != nil {
			return err
		}
	}
```

**File:** executors/instance/instance.go (L139-145)
```go
	return autoscaler.New(executors.DefaultExecutorProvider{
		Creator:          creator,
		FeaturesUpdater:  featuresUpdater,
		DefaultShellName: options.Shell.Shell,
	}, autoscaler.Config{
		MapJobImageToVMImage: true,
	})
```

**File:** common/allowed_images.go (L20-26)
```go
func VerifyAllowedImage(options VerifyAllowedImageOptions, logger buildlogger.Logger) error {
	for _, allowedImage := range options.AllowedImages {
		ok, _ := doublestar.Match(allowedImage, options.Image)
		if ok {
			return nil
		}
	}
```

**File:** executors/internal/autoscaler/acquisition.go (L200-221)
```go

	// use nesting config defined image, unless the executor allows for the
	// job image to override.
	image := nestingCfg.Image
	if options.Build.Image.Name != "" && ref.mapJobImageToVMImage {
		image = options.Build.Image.Name
	}

	image = options.Build.GetAllVariables().ExpandValue(image)
	if image == "" {
		return nil, errNoNestingImageSpecified
	}

	logger.Println("Creating nesting VM", image)

	// create vm
	var vm hypervisor.VirtualMachine
	var stompedVMID *string
	var err error
	err = withInit(ctx, options.Config, nc, func() error {
		slot := int32(ref.AcquisitionSlot())
		vm, stompedVMID, err = nc.Create(ctx, image, &slot)
```
