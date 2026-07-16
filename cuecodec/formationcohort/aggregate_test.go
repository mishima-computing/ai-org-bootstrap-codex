package formationcohort

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
)

func contentDigest(content string) string {
	digest := sha256.Sum256([]byte(content))
	return hex.EncodeToString(digest[:])
}

func validRequest() Request {
	carriers := make([]Carrier, 0, len(carrierOrder))
	contracts := Contracts()
	for index, id := range carrierOrder {
		carriers = append(carriers, Carrier{
			ID:           id,
			Source:       contracts[index*2],
			Target:       contracts[index*2+1],
			DerivationID: "derivation:" + id + ":1", Links: []string{"parent:1"},
			Citations: []Citation{{ID: "source", URL: "https://example.com/source"}}, BuilderID: BuilderID,
			ContentSHA256: contentDigest(`{"number":1.00,"rows":[2,1]}`),
			FragmentMode:  "complete", Guard: GuardResult{FragmentIDs: []string{}},
			Content: json.RawMessage(`{"number":1.00,"rows":[2,1]}`),
		})
	}
	stages := make([]StageReceipt, 0, len(stageOrder))
	for _, stage := range stageOrder {
		stages = append(stages, StageReceipt{Stage: stage, CarrierIDs: append([]string(nil), carrierOrder[:]...), BuilderID: BuilderID})
	}
	return Request{Carriers: carriers, Stages: stages}
}

func TestRejectLifecycleWhenOneCarrierMissesTargetedReform(t *testing.T) {
	request := validRequest()
	request.Stages[1].CarrierIDs = request.Stages[1].CarrierIDs[:3]
	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected cohort-split rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "lifecycle-order" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestAssembleJSONEstablishesClosedCohortTransition(t *testing.T) {
	source, err := json.Marshal(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	aggregate, err := AssembleJSON(source)
	if err != nil {
		t.Fatal(err)
	}
	if !aggregate.Validated || len(aggregate.Carriers) != len(carrierOrder) || len(aggregate.TemporaryArtifacts) != 0 {
		t.Fatalf("serialized transition did not establish the complete clean cohort: %#v", aggregate)
	}
}

func TestAssembleJSONRejectsUnknownOrDuplicateContractCoordinates(t *testing.T) {
	source, err := json.Marshal(validRequest())
	if err != nil {
		t.Fatal(err)
	}

	unknown := bytes.Replace(source, []byte(`"variant":"formation-source"`), []byte(`"variant":"formation-source","unregistered":true`), 1)
	aggregate, err := AssembleJSON(unknown)
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "request-contract" {
		t.Fatalf("unexpected unknown-field error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped unknown-field rejection: %#v", aggregate)
	}

	duplicate := bytes.Replace(source, []byte(`"variant":"formation-source"`), []byte(`"variant":"formation-source","variant":"formation-target"`), 1)
	aggregate, err = AssembleJSON(duplicate)
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "request-json" {
		t.Fatalf("unexpected duplicate-field error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped duplicate-field rejection: %#v", aggregate)
	}
}

func TestEstablishCohortPreservesProvenanceAndExactContent(t *testing.T) {
	request := validRequest()
	request.Carriers[1].FragmentMode = "ordered_shallow"
	request.Carriers[1].Content = nil
	request.Carriers[1].Guard = GuardResult{Measured: true, RequiresFragments: true, FragmentIDs: []string{"a", "b"}}
	request.Carriers[1].Fragments = []json.RawMessage{json.RawMessage(`{"z":1.20}`), json.RawMessage(`{"a":[3,2]}`)}
	request.Carriers[1].ContentSHA256 = contentDigest(`{"a":[3,2],"z":1.20}`)

	aggregate, err := Assemble(request)
	if err != nil {
		t.Fatal(err)
	}
	if !aggregate.Validated || len(aggregate.TemporaryArtifacts) != 0 || aggregate.SHA256 == "" {
		t.Fatalf("not established: %#v", aggregate)
	}
	if len(aggregate.CarrierSHA256) != 4 {
		t.Fatalf("missing complete carrier digest evidence: %#v", aggregate.CarrierSHA256)
	}
	if len(aggregate.SchemaGuardResults) != 4 ||
		!reflect.DeepEqual(aggregate.SchemaGuardResults[1], request.Carriers[1].Guard) ||
		!reflect.DeepEqual(aggregate.Carriers[1].Guard, request.Carriers[1].Guard) ||
		aggregate.Carriers[1].FragmentMode != "ordered_shallow" {
		t.Fatalf("schema guard result was not preserved: %#v", aggregate)
	}
	if string(aggregate.Carriers[1].Content) != `{"a":[3,2],"z":1.20}` {
		t.Fatalf("exact aggregate content lost: %s", aggregate.Carriers[1].Content)
	}
	for index, carrier := range aggregate.Carriers {
		input := request.Carriers[index]
		if carrier.DerivationID != input.DerivationID || carrier.BuilderID != input.BuilderID ||
			!reflect.DeepEqual(carrier.Links, input.Links) || !reflect.DeepEqual(carrier.Citations, input.Citations) {
			t.Fatalf("provenance changed for %s", carrier.ID)
		}
	}
}

func TestSourceAndTargetContractsAreIndependentlyRegistered(t *testing.T) {
	contracts := Contracts()
	if len(contracts) != 8 {
		t.Fatalf("got %d contracts", len(contracts))
	}
	for index := 0; index < len(contracts); index += 2 {
		source, target := contracts[index], contracts[index+1]
		if source.Context == target.Context || source.Kind != target.Kind ||
			source.Variant != "formation-source" || target.Variant != "formation-target" {
			t.Fatalf("contracts not independently registered: %#v %#v", source, target)
		}
		registeredSource, registeredTarget, err := ContractsFor(carrierOrder[index/2])
		if err != nil || registeredSource != source || registeredTarget != target {
			t.Fatalf("carrier contract lookup disagrees with registry: %#v %#v %v", registeredSource, registeredTarget, err)
		}
	}
	if source, target, err := ContractsFor("unknown"); err == nil ||
		source != (Contract{}) || target != (Contract{}) {
		t.Fatalf("unknown carrier returned contracts: %#v %#v %v", source, target, err)
	}
}

func TestCarrierRecipesAreIndependentlyTypedAndDetached(t *testing.T) {
	carrier := validRequest().Carriers[1]
	carrier.FragmentMode = "ordered_shallow"
	carrier.Content = nil
	carrier.Guard = GuardResult{
		Measured: true, RequiresFragments: true, FragmentIDs: []string{"domain:one"},
	}
	carrier.Fragments = []json.RawMessage{json.RawMessage(`{"rows":[1,2]}`)}

	invocation := carrier.InvocationRecipe()
	fragments := carrier.FragmentRecipe()
	if invocation.CarrierID != fragments.CarrierID || invocation.Source == invocation.Target {
		t.Fatalf("recipes lost independent carrier coordinates: %#v %#v", invocation, fragments)
	}
	fragments.Guard.FragmentIDs[0] = "changed"
	fragments.Fragments[0][2] = 'X'
	if carrier.Guard.FragmentIDs[0] != "domain:one" || string(carrier.Fragments[0]) != `{"rows":[1,2]}` {
		t.Fatal("typed recipe projection mutated its source carrier")
	}
}

func TestRejectCarrierWhenTypedInvocationUsesAnotherTargetsContract(t *testing.T) {
	request := validRequest()
	request.Carriers[0].Target = request.Carriers[1].Target

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected independently typed target rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "independent-contracts" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestRejectAggregateForUnguardedFragmentsReturnsNoPartialValue(t *testing.T) {
	request := validRequest()
	request.Carriers[1].FragmentMode = "ordered_shallow"
	request.Carriers[1].Content = nil
	request.Carriers[1].Fragments = []json.RawMessage{json.RawMessage(`{"a":1}`)}
	request.Carriers[1].Guard = GuardResult{RequiresFragments: true, FragmentIDs: []string{"a"}}

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "fragment-guard" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestRejectCompleteCarrierWithOrphanedFragmentIdentity(t *testing.T) {
	request := validRequest()
	request.Carriers[0].Guard = GuardResult{
		Measured:    true,
		FragmentIDs: []string{"stale-measurement-fragment"},
	}

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected complete-mode fragment identity rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "complete-content" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestRejectCarrierWhenInvocationDigestDoesNotMatchEstablishedContent(t *testing.T) {
	request := validRequest()
	request.Carriers[0].ContentSHA256 = "0000000000000000000000000000000000000000000000000000000000000000"

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected source digest rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "source-digest" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestRejectCarrierWhenInvocationDigestIsNotCanonicalLowercase(t *testing.T) {
	request := validRequest()
	request.Carriers[0].Content = json.RawMessage(`{"value":"uppercase-digest"}`)
	request.Carriers[0].ContentSHA256 = contentDigest(`{"value":"uppercase-digest"}`)
	request.Carriers[0].ContentSHA256 = string(bytes.ToUpper([]byte(request.Carriers[0].ContentSHA256)))

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected non-canonical digest rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "source-digest" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestEstablishCohortRejectsEmptyProvenanceLink(t *testing.T) {
	request := validRequest()
	request.Carriers[2].Links = []string{""}

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected empty provenance link rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.CarrierID != "spine-art-bible" || cohortErr.Rule != "provenance" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestRejectOrderedFragmentsWithDuplicateInvocationIdentity(t *testing.T) {
	request := validRequest()
	request.Carriers[1].FragmentMode = "ordered_shallow"
	request.Carriers[1].Content = nil
	request.Carriers[1].Guard = GuardResult{Measured: true, RequiresFragments: true, FragmentIDs: []string{"same", "same"}}
	request.Carriers[1].Fragments = []json.RawMessage{json.RawMessage(`{"z":1}`), json.RawMessage(`{"a":2}`)}
	request.Carriers[1].ContentSHA256 = contentDigest(`{"a":2,"z":1}`)

	aggregate, err := Assemble(request)
	if err == nil {
		t.Fatal("expected fragment identity rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "fragment-identity" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(aggregate, Aggregate{}) {
		t.Fatalf("partial aggregate escaped: %#v", aggregate)
	}
}

func TestProjectEveryLifecycleStageCarriesDetachedCompleteCohort(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	for _, stage := range stageOrder {
		projection, err := ProjectStage(aggregate, stage)
		if err != nil {
			t.Fatalf("project %s: %v", stage, err)
		}
		if projection.Stage != stage || projection.BuilderID != BuilderID || len(projection.Carriers) != len(carrierOrder) {
			t.Fatalf("incomplete %s projection: %#v", stage, projection)
		}
		for index, carrier := range projection.Carriers {
			if carrier.ID != carrierOrder[index] {
				t.Fatalf("%s split or reordered cohort: %#v", stage, projection.Carriers)
			}
			source, target, err := ContractsFor(carrier.ID)
			if err != nil || carrier.Source != source || carrier.Target != target {
				t.Fatalf("%s changed registered contracts for %s: %#v %#v %v", stage, carrier.ID, carrier.Source, carrier.Target, err)
			}
		}
		projection.Carriers[0].Content[0] = '['
		projection.Carriers[0].Links[0] = "changed"
		if aggregate.Carriers[0].Content[0] != '{' || aggregate.Carriers[0].Links[0] != "parent:1" {
			t.Fatalf("%s projection aliases established aggregate", stage)
		}
	}
}

func TestProjectStageRejectsUnknownOrTamperedAggregateWithoutPartialCohort(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}

	projection, err := ProjectStage(aggregate, "submission")
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "lifecycle-stage" {
		t.Fatalf("unexpected unknown-stage error: %v", err)
	}
	if !reflect.DeepEqual(projection, StageProjection{}) {
		t.Fatalf("partial unknown-stage projection escaped: %#v", projection)
	}

	aggregate.Carriers[0].DerivationID = "tampered"
	projection, err = ProjectStage(aggregate, "review")
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "aggregate-digest" {
		t.Fatalf("unexpected tampered-aggregate error: %v", err)
	}
	if !reflect.DeepEqual(projection, StageProjection{}) {
		t.Fatalf("partial tampered projection escaped: %#v", projection)
	}
}

func TestLoadContributorDoesNotMutateEstablishedAggregate(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	wantDigest := aggregate.SHA256
	carrier, err := LoadContributor(aggregate, "spine-art-bible")
	if err != nil {
		t.Fatal(err)
	}
	if carrier.ID != "spine-art-bible" || aggregate.SHA256 != wantDigest {
		t.Fatal("load transition mutated aggregate")
	}
	carrier.Content[0] = '['
	carrier.Links[0] = "changed"
	carrier.Guard.FragmentIDs = append(carrier.Guard.FragmentIDs, "changed")
	if aggregate.Carriers[2].Content[0] != '{' || aggregate.Carriers[2].Links[0] != "parent:1" {
		t.Fatal("loaded contributor aliases established aggregate storage")
	}
	if len(aggregate.Carriers[2].Guard.FragmentIDs) != 0 {
		t.Fatal("loaded contributor aliases established schema guard evidence")
	}
}

func TestValidateAggregateRejectsDroppedSchemaGuardResult(t *testing.T) {
	request := validRequest()
	request.Carriers[1].FragmentMode = "ordered_shallow"
	request.Carriers[1].Content = nil
	request.Carriers[1].Guard = GuardResult{Measured: true, RequiresFragments: true, FragmentIDs: []string{"domain:one"}}
	request.Carriers[1].Fragments = []json.RawMessage{json.RawMessage(`{"rows":[1,2]}`)}
	request.Carriers[1].ContentSHA256 = contentDigest(`{"rows":[1,2]}`)
	aggregate, err := Assemble(request)
	if err != nil {
		t.Fatal(err)
	}
	aggregate.SchemaGuardResults[1].FragmentIDs = nil
	// Re-signing the surrounding representation must not make missing guard
	// evidence valid; the cross-body guard comparison remains authoritative.
	aggregate.SHA256, err = aggregateDigest(aggregate)
	if err != nil {
		t.Fatal(err)
	}
	err = ValidateAggregate(aggregate)
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "schema-guard-evidence" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidateAggregatePreflightsWholeCohortWithoutMutation(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	wantDigest := aggregate.SHA256
	wantContent := append(json.RawMessage(nil), aggregate.Carriers[1].Content...)

	if err := ValidateAggregate(aggregate); err != nil {
		t.Fatal(err)
	}
	if aggregate.SHA256 != wantDigest || !reflect.DeepEqual(aggregate.Carriers[1].Content, wantContent) {
		t.Fatal("aggregate validation mutated established state")
	}
}

func TestValidateAggregateRejectsBrokenLifecycleCohort(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	aggregate.Stages[1].CarrierIDs = aggregate.Stages[1].CarrierIDs[:3]

	err = ValidateAggregate(aggregate)
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "lifecycle-order" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidateAggregateRejectsEmptyPreservedLink(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	aggregate.Carriers[2].Links[0] = ""
	aggregate.SHA256, err = aggregateDigest(aggregate)
	if err != nil {
		t.Fatal(err)
	}

	err = ValidateAggregate(aggregate)
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.CarrierID != "spine-art-bible" || cohortErr.Rule != "provenance" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestLoadContributorRejectsTamperedCarrierWithoutReturningPartialValue(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	aggregate.Carriers[2].DerivationID = "changed"

	carrier, err := LoadContributor(aggregate, "spine-art-bible")
	if err == nil {
		t.Fatal("expected tampered aggregate rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "aggregate-digest" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(carrier, EstablishedCarrier{}) {
		t.Fatalf("partial contributor escaped: %#v", carrier)
	}
}

func TestLoadContributorRejectsCarrierDigestMismatch(t *testing.T) {
	aggregate, err := Assemble(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	aggregate.CarrierSHA256[1] = "0000000000000000000000000000000000000000000000000000000000000000"

	carrier, err := LoadContributor(aggregate, "domain-specification")
	if err == nil {
		t.Fatal("expected digest evidence rejection")
	}
	var cohortErr *Error
	if !errors.As(err, &cohortErr) || cohortErr.Rule != "aggregate-digest" {
		t.Fatalf("unexpected error: %v", err)
	}
	if !reflect.DeepEqual(carrier, EstablishedCarrier{}) {
		t.Fatalf("partial contributor escaped: %#v", carrier)
	}
}
