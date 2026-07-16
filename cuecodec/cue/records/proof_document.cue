package records

import "github.com/mishima-computing/cuecodec/cue/schema"

// Keep the authoritative schema import in the proven offline closure while
// leaving stage ordering to the Go codec: identity is inspected before the
// record is explicitly unified with this definition.
#proof_document_schema: schema.#ProofDocument

proof_document_precision: {
	apiVersion: "cuecodec.mishima-computing.github.io/v1"
	kind:       "ProofDocument"
	stable_id:  "proof-document:precision-proof"

	metadata: {
		title:  "Precision-preserving admission proof"
		labels: ["canonical", "offline", "v1"]
	}

	spec: {
		enabled:       true
		large_integer: 900719925474099312345678901234567890
		exact_decimal: 0.1000000000000000000000000001
		exponent:      1.234567890123456789e+42
		ordered_steps: [
			{name: "third", weight: 3},
			{name: "first", weight: 1},
			{name: "second", weight: 2},
		]
		nested: {
			owner: {
				name:   "codec-admission"
				active: true
			}
			rank_sequence: [3, 1, 2]
		}
	}
}
