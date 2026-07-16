package records

import "github.com/mishima-computing/cuecodec/cue/schema"

// Keep this record's schema import in the proven offline closure while the Go
// codec retains identity-before-unification stage ordering.
#proof_document_release_schema: schema.#ProofDocument

proof_document_release: {
	apiVersion: "cuecodec.mishima-computing.github.io/v1"
	kind:       "ProofDocument"
	stable_id:  "proof-document:release-readiness"

	metadata: {
		title:  "Release readiness admission proof"
		labels: ["admission", "release", "v1"]
	}

	spec: {
		enabled:       false
		large_integer: -900719925474099312345678901234567891
		exact_decimal: 123456789.0000000000000000000001
		exponent:      9.876543210987654321e-42
		ordered_steps: [
			{name: "verify", weight: 8},
			{name: "publish", weight: 13},
			{name: "observe", weight: 5},
		]
		nested: {
			owner: {name: "release-engineering", active: false}
			rank_sequence: [8, 13, 5]
		}
	}
}
