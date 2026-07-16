package schema

// #SemanticStatus is the established logical four-field projection. Empty
// strings remain valid for compatibility with the pre-codec Semantic Status
// API; consumers that require stronger semantics own that later policy.
#SemanticStatus: close({
	change_kind!:   string
	subsystem!:     string
	owner!:         string
	working_state!: string
})

// #SemanticStatusEnvelope is the sole writable representation.  No identity
// or formatter metadata may be added to this closed four-key envelope.
#SemanticStatusEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "SemanticStatus"
	variant!:    "git-note"
	body!:       #SemanticStatus
})

// Patchwork Check events remain one opaque byte sequence. Replay products are
// intentionally absent from this closed body.
#PatchworkCheckEventStream: close({
	raw_stream_base64!: string & =~"^[A-Za-z0-9+/]*={0,2}$"
	source_oid!:        string & =~"^[0-9a-f]{40,64}$"
	source_path!:       string & !="" & !~"^/"
})

#PatchworkCheckEventStreamEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "PatchworkCheckEventStream"
	variant!:    "network-node"
	body!:       #PatchworkCheckEventStream
})

// Authoring announcements use one body vocabulary with two frozen address
// variants.  Identity and lineage fields are deliberately duplicated in the
// body so consumers can verify them against the ref and commit before routing.
#AuthoringAnnouncement: close({
	schema!:                      "ai-org-authoring-announcement-v2"
	series_branch!:               string & =~"^ai-org/patch-series/"
	node_path!:                   "." | (string & =~"^sub/[a-z0-9_]+$")
	node_address!:                string & !=""
	series_head_at_announcement!: string & =~"^[0-9a-f]{40,64}$"
	parent!:                      string & =~"^[0-9a-f]{40,64}$"
	contrib_branch!:              string & =~"^ai-org/contrib/"
	canonical_ref!:               string & =~"^refs/ai-org/authoring-announcements/v2/[0-9a-f]{64}$"
	author!: close({name!: string & !="", email!: string & =~"^[^@]+@users\\.noreply\\.github\\.com$"})
	status!: "authoring"
})

#RootAnnouncementEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "AuthoringAnnouncement"
	variant!:    "root-announcement"
	body!: #AuthoringAnnouncement & {node_path: "."}
})

#ChildAnnouncementEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "AuthoringAnnouncement"
	variant!:    "child-announcement"
	body!: #AuthoringAnnouncement & {node_path: string & =~"^sub/[a-z0-9_]+$"}
})

// The terminal request-outcome cohort is published as one immutable custom-ref
// tree. These definitions admit the two bodies independently; the Python Git
// boundary enforces their request_id pairing before either becomes reachable.
#RequestProvenanceRecord: close({
	request_id!:     string
	payload_sha256!: string & =~"^[0-9a-f]{64}$"
	raw_request!:    string
	request_payload!: {...}
	memento!: string
})

#RequestProvenanceEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "RequestProvenanceRecord"
	variant!:    "request-outcome-custom-ref"
	body!:       #RequestProvenanceRecord
})

#RequestOutcome: close({
	request_id!: string
	status!:     string
	result!: {...}
	memento!: string
})

#RequestOutcomeEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "RequestOutcome"
	variant!:    "request-outcome-custom-ref"
	body!:       #RequestOutcome
})

// The first patch-series branch cohort admits the root work-order view and its
// request provenance together. Nested patch-series nodes remain on their
// historical JSON representation until their own cohort is admitted.
_patchSeriesCoverLetterFields: {
	raw_request!:                 string
	working_title!:               string
	request_type!:                string
	problem_or_motivation!:       string
	intended_users_or_jobs!:      string
	desired_outcomes_success!:    string
	affected_area_platform!:      string
	tech_stack!: close({
		build_strategy!: string
		engine!:         string
		framework!:      string
		language!:       string
		platform!:       string
		rationale!:      string
		provenance!:     string
	})
	user_experience_requirements!: close({
		applicability!: close({applicability!: string, not_user_facing_reason!: string})
		experience_identity!: close({named_reference!: string, genre_conventions!: string, must_resemble!: string, must_not_resemble!: string})
		presentation_model!: close({camera_and_view!: string, world_readability!: string, ui_taxonomy_notes!: string})
		core_status_surfaces!: close({player_status!: string, opposition_status!: string, inventory_resources!: string, objective_progress!: string, location_identity!: string})
		entity_affordances!: close({interactive_entities!: string, exits_and_transitions!: string, gates_and_locks!: string, hazards_and_bosses!: string, collectibles!: string, decorative_elements!: string})
		action_feedback_matrix!: [...close({action_verb!: string, feedback_requirement!: string})]
		progression_legibility!: close({current_goal_visibility!: string, locked_state_feedback!: string, unlocked_state_feedback!: string, flag_observability!: string, ending_state_consistency!: string})
		hud_and_ui_flow!: close({primary_hud!: string, secondary_screens!: string, menu_flow!: string, dialog_flow!: string, failure_and_recovery!: string})
		visual_language_constraints!: close({contrast!: string, palette_role!: string, silhouette_readability!: string, labels_and_markers!: string, animation_minimums!: string})
		accessibility_baseline!: close({controls!: string, text_readability!: string, color_independence!: string, audio_independence!: string, pacing!: string})
		acceptance_tests!: close({screenshot_checks!: [...string], interaction_checks!: [...string], playtest_checks!: [...string]})
	})
	background_facts!:            string
	constraints_assumptions!:     [...string]
	references!:                  [...string]
	grounding_provenance!:        string
	open_questions!:              [...string]
	non_goals_out_of_scope!:      [...string]
	proposal_hint!:               string
	alternatives_considered!:     [...string]
}

#PatchSeriesCoverLetter: close({_patchSeriesCoverLetterFields})

// Child producer-goal carriers may add a row-thin commission request while
// ordinary child requests and the guarded root structured-output projection
// retain the established cover-letter shape.
#CommissionRequest: close({
	schema!: string
	spine_contract_refs!: [...close({role!: string, path!: string})]
	identity_manifest_fields!: [...close({child_key!: string, field!: string})]
	acceptance_checks!: [...string]
	contract_fields!: [...close({
		child_key!: string
		field!: string
		value!: string
	})]
})

#ChildPatchSeriesCoverLetter: close({
	_patchSeriesCoverLetterFields
	commission_request?: #CommissionRequest
})

#PatchSeriesCoverLetterEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "PatchSeriesCoverLetter"
	variant!:    "patch-series-root"
	body!:       #PatchSeriesCoverLetter
})

#BranchRequestProvenanceRecord: #RequestProvenanceRecord

#BranchRequestProvenanceEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "RequestProvenanceRecord"
	variant!:    "patch-series-branch"
	body!:       #BranchRequestProvenanceRecord
})

// Review and reform records deliberately keep their established aggregate
// members open while requiring every stable top-level property. This preserves
// the Python dict/list carrier shapes and permits older records to carry
// additional, already-published review evidence.
#DirectionReviewRound: {
	patch_series_id!: string
	branch!:          string
	round!:           int & >=1
	inputs!: {...}
	axis_reviews!: [...{...}]
	objections!: [...{...}]
	per_axis_verdicts!: {...}
	consolidation!: {...}
	verdict!:           string
	git_result_marker!: string
	serial!:            string
	deferred!: [...string]
	...
}

#DirectionReviewRoundEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "DirectionReviewRound"
	variant!:    "review-reform-cohort"
	body!:       #DirectionReviewRound
})

#AuthorResponseRecord: {
	patch_series_id!: string
	review_round!:    int & >=1
	author_version!:  int & >=1
	affected_steps!: [...string]
	changed_node_ids!: [...string]
	requester_assumption_notes!: [...{...}]
	objections!: [...{...}]
	memento!: [...string]
	...
}

#AuthorResponseRecordEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "AuthorResponseRecord"
	variant!:    "review-reform-cohort"
	body!:       #AuthorResponseRecord
})

#RevisionDeltaRecord: {
	patch_series_id!: string
	review_round!:    int & >=1
	author_version!:  int & >=1
	added_node_ids!: [...string]
	changed_node_ids!: [...string]
	pruned_node_ids!: [...string]
	node_replacements!: [...{...}]
	objection_responses!: [...{...}]
	memento!: [...string]
	...
}

#RevisionDeltaRecordEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "RevisionDeltaRecord"
	variant!:    "review-reform-cohort"
	body!:       #RevisionDeltaRecord
})

// The network publication cohort moves as one Git tree while retaining an
// independently typed contract for each durable member. Root and child
// manifests intentionally share field vocabulary but have distinct variants
// and write scopes.
#NetworkEdge: close({
	type!:     string
	to?:       string
	with?:     string
	state?:    string
	or_group?: string
	when?:     string
	reason?:   string
})

#ManifestChild: close({
	child_key!:        string
	node_path!:        string
	lifecycle_status!: string
	edges!: [...#NetworkEdge]
})

#Ownership: close({request_owner!: string, interior_owner!: string})

#RootNetworkNodeManifest: close({
	schema!:               "patch_series-network-node-v1"
	identity_stage!:       string
	node_path!:            "."
	branch!:               string
	relation_from_parent!: "root"
	lifecycle_status!:     string
	ownership!:            #Ownership
	write_scope!: close({allowed_subtree!: "."})
	scope_item_ids!: [...string]
	children!: [...#ManifestChild]
	serial_id?: string
	parent_retained_scope_ids?: [...string]
	declared_patchwork_checks?: [...close({name!: string, type!: "counter" | "text"})]
})

#RootNetworkNodeManifestEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "NetworkNodeManifest"
	variant!:    "network-root"
	body!:       #RootNetworkNodeManifest
})

#ChildNetworkNodeManifest: close({
	schema!:               "patch_series-network-node-v1"
	identity_stage!:       string
	id!:                   string
	child_key!:            string
	node_path!:            string & =~"^sub/"
	parent_branch!:        string
	relation_from_parent!: "split-into"
	split_operator!:       "AND"
	edges!: [...#NetworkEdge]
	scope_item_ids!: [...string]
	lifecycle_status!: string
	ownership!:        #Ownership
	write_scope!: close({allowed_subtree!: string & =~"^sub/"})
	contrib_branch!:   string
	serial_id?:        string
	parent_node_path?: "."
	title?:            string
	summary?:          string
	acceptance_criteria?: [...string]
	functional_check?: string
	systems?: [...string]
	ux_acceptance_tests?: [...string]
	risks?: [...string]
	stamping?: close({template_id!: string, author!: string, scale_row!: int})
	stamping_provenance?: close({
		template_id!: string
		scale_table_row!: {[string]: _}
		stamping_author_signature!: string
	})
	escalation_evidence?: {[string]: _}
	stale_reason?:                    string
	stale_ledger_commit?:             string
	stale_previous_lifecycle_status?: string
	ledger_commit?:                   string
	revalidated_against_parent?:      string
	parent_rebaseline_ledger_commit?: string
})

#ScopeItem: close({id!: string, kind!: string, text!: string})
#CoverageItem: close({scope_item_id!: string, owner!: string, owner_node_path!: string})
#LedgerChild: close({
	id!:               string
	serial_id!:        string
	child_key!:        string
	node_path!:        string
	address!:          string
	allowed_subtree!:  string
	contrib_branch!:   string
	lifecycle_status!: string
	edges!: [...#NetworkEdge]
	scope_item_ids!: [...string]
})

#ChildNetworkNodeManifestEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "NetworkNodeManifest"
	variant!:    "network-child"
	body!:       #ChildNetworkNodeManifest
})

#SeriesCoverageLedger: close({
	schema!:                   "series-coverage-ledger-v1"
	ledger_revision!:          int & >=1
	supersedes_ledger_commit!: string
	rebaselined_from_escalation!: {[string]: _}
	parent_branch!:  string
	relation!:       "split-into" | "right-sized" | "stamped-children"
	split_operator!: "AND" | "LEAF"
	scope_items!: [...#ScopeItem]
	coverage!: [...#CoverageItem]
	parent_retained_scope_ids!: [...string]
	children!: [...#LedgerChild]
})

#SeriesCoverageLedgerEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "SeriesCoverageLedger"
	variant!:    "network-root"
	body!:       #SeriesCoverageLedger
})


// SeriesScopeDecomposition is a read-only, digest-bound projection. Historical
// coverage ledgers remain inputs; this final body adds producer obligations to
// their established scope partition without becoming a replacement ledger.
#EstablishedSeriesScopeItem: close({
	id!:   string & !=""
	kind!: "desired_outcome" | "referee_goal" | "goal" | "ux_acceptance_test" | "patch_plan" | "must_address_risk" | "domain_specification"
	text!: string & !=""
})

#ProductionObligationScopeItem: close({
	id!:                         string & !=""
	kind!:                       "production_obligation"
	text!:                       string & !=""
	production_obligation_id!:   string & !=""
	referee_goal_id!:            string & !=""
	deliverable_requirement_id!: string & !=""
	patch_plan_item_id!:         string & !=""
	deliverable!:                string & !=""
	eligibility_predicate!:      string & !=""
})

#SeriesScopeItem: #EstablishedSeriesScopeItem | #ProductionObligationScopeItem

#SeriesScopeOwnership: close({
	scope_item_id!:   string & !=""
	owner!:           string & !=""
	owner_node_path!: string & !=""
})

#EscalationRebaselineRecord: close({
	review_round!:      int & >=1
	child_branch!:      string & !=""
	git_result_commit!: string & =~"^[0-9a-f]{40}$"
})

#SeriesScopeAllocationChecks: {
	schema:                   "series-scope-decomposition-v1"
	frozen_root_oid:          string & !=""
	canonical_root_sha256:    string & =~"^[0-9a-f]{64}$"
	historical_ledger_schema: "series-coverage-ledger-v1"
	ledger_revision:          int & >=1
	supersedes_ledger_commit: string & (*"" | =~"^[0-9a-f]{40}$")
	rebaselined_from_escalation: close({}) | #EscalationRebaselineRecord
	scope_items: [...#SeriesScopeItem]
	ownership: [...#SeriesScopeOwnership]
	parent_retained_scope_ids: [...string & !=""]

	if supersedes_ledger_commit == "" {
		ledger_revision: 1
	}
	if supersedes_ledger_commit != "" {
		ledger_revision: int & >=2
	}

	for item in scope_items {
		let matchingItems = [for candidate in scope_items if candidate.id == item.id {candidate}]
		let matchingOwners = [for owner in ownership if owner.scope_item_id == item.id {owner}]
		if len(matchingItems) != 1 {
			_duplicate_scope_identity: _|_
		}
		if len(matchingOwners) != 1 {
			_inexact_scope_ownership: _|_
		}
		if item.kind == "production_obligation" {
			let matchingObligations = [for candidate in scope_items if candidate.kind == "production_obligation" {
				if candidate.production_obligation_id == item.production_obligation_id {
					candidate
				}
			}]
			let matchingReferees = [for candidate in scope_items if candidate.kind == "production_obligation" {
				if candidate.referee_goal_id == item.referee_goal_id {candidate}
			}]
			let matchingRequirements = [for candidate in scope_items if candidate.kind == "production_obligation" {
				if candidate.deliverable_requirement_id == item.deliverable_requirement_id {candidate}
			}]
			if item.id != item.production_obligation_id || item.text != item.deliverable || len(matchingObligations) != 1 || len(matchingReferees) != 1 || len(matchingRequirements) != 1 {
				_inexact_production_obligation_scope: _|_
			}
		}
	}
	for owner in ownership {
		if len([for item in scope_items if item.id == owner.scope_item_id {item}]) != 1 {
			_fabricated_scope_ownership: _|_
		}
	}
	for retainedID in parent_retained_scope_ids {
		if len([for item in scope_items if item.id == retainedID {item}]) != 1 {
			_unknown_parent_retained_scope: _|_
		}
		if len([for candidate in parent_retained_scope_ids if candidate == retainedID {candidate}]) != 1 {
			_duplicate_parent_retained_scope: _|_
		}
	}
}

#SeriesScopeDecomposition: close({
	schema!:                   "series-scope-decomposition-v1"
	frozen_root_oid!:          string & =~"^[0-9a-f]{40}$"
	canonical_root_sha256!:    string & =~"^[0-9a-f]{64}$"
	historical_ledger_schema!: "series-coverage-ledger-v1"
	ledger_revision!:          int & >=1
	supersedes_ledger_commit!: string & (*"" | =~"^[0-9a-f]{40}$")
	rebaselined_from_escalation!: *close({}) | #EscalationRebaselineRecord
	scope_items!: [...#SeriesScopeItem]
	ownership!: [...#SeriesScopeOwnership]
	parent_retained_scope_ids!: [...string & !=""]
} & #SeriesScopeAllocationChecks)

#SeriesScopeDecompositionEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "SeriesScopeDecomposition"
	variant!:    "patch-series-root-preview"
	body!:       #SeriesScopeDecomposition
})
#PatchQueueStatusRollup: close({
	schema!:         "patch-queue-status-rollup-v1"
	generated!:      true
	generated_from!: "patch-series-manifest.json"
	ledger_schema!:  string
	node_path!:      "."
	children!: [...#ManifestChild]
	coverage!: [...#CoverageItem]
})

#PatchQueueStatusRollupEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "PatchQueueStatusRollup"
	variant!:    "network-control-read"
	body!:       #PatchQueueStatusRollup
})

#MaintainerSeriesRequest: close({
	id!: string
	submitted_at!: string
	request!: #PatchSeriesCoverLetter
	commission_request!: #CommissionRequest
	provenance!: close({
		requester!:        "parent"
		parent_branch!:    string
		parent_node_path!: "."
		child_key!:        string
	})
})

#MaintainerSeriesRequestEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "MaintainerSeriesRequest"
	variant!:    "network-child"
	body!:       #MaintainerSeriesRequest
})

#PatchSeriesMetadata: close({
	schema!:               "patch_series-network-node-v1"
	lifecycle_status!:     string
	id?:                   string
	serial_id?:            string
	parent_branch?:        string
	child_key?:            string
	ledger_commit?:        string
	relation_from_parent?: string
	split_operator?:       string
	edges?: [...#NetworkEdge]
	scope_item_ids?: [...string]
	stale_reason?:                    string
	stale_ledger_commit?:             string
	revalidated_against_parent?:      string
	parent_rebaseline_ledger_commit?: string
})

#PatchSeriesMetadataEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "PatchSeriesMetadata"
	variant!:    "network-root-and-child"
	body!:       #PatchSeriesMetadata
})

#ChildPatchSeriesCoverLetterEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!: "PatchSeriesCoverLetter"
	variant!: "patch-series-child"
	body!: #ChildPatchSeriesCoverLetter
})

// The canonical root Technical Approach is admitted for read-only preview.
// Its established derivation tree stays intact while the producer-aware
// records under problem are closed. Cross-record identity, lineage, and exact
// patch-plan partition checks are repeated by the deterministic preview vet.
#RootProducerAwareGoal: close({
	id!: string & != ""
	requires_deliverable!: bool
	actor!: string
	capability!: close({
		action!: string
		preconditions!: [...string]
	})
	verifiable_outcome!: close({
		expected_state!: string
		evidence!: string
	})
	verification!: close({
		method!: "automated_test" | "manual_check" | "metric"
		check!: string
	})
	ux_trace!: string
})

#RootDeliverableRequirement: close({
	id!: string & != ""
	referee_goal_id!: string & != ""
	production_obligation_id!: string & != ""
	deliverable!: string & != ""
})

#RootObligationReplacementLink: close({
	replaces_obligation_id!: string & != ""
	replacement_reason!: string & != ""
})

#RootProductionObligation: close({
	id!: string & != ""
	referee_goal_id!: string & != ""
	deliverable_requirement_id!: string & != ""
	deliverable!: string & != ""
	eligibility_predicate!: string & != ""
	replacement_links!: [] | [#RootObligationReplacementLink]
})

#RootPatchPlanAssignment: close({
	item_id!: string & != ""
	production_obligation_ids!: [...string & != ""]
})

#RootTechnicalApproachTree: close({
	problem!: {
		id!: string & != ""
		problem!: string
		affected!: string
		current_inadequacy!: string
		goals!: [...#RootProducerAwareGoal]
		non_goals!: [...string]
		constraints!: {...}
		prior_art!: [...{...}]
		question!: {...}
		open_questions!: [...string]
		deliverable_requirements!: [...#RootDeliverableRequirement]
		production_obligations!: [...#RootProductionObligation]
		patch_plan!: [...#RootPatchPlanAssignment]

		// A true referee goal has exactly one cross-linked producer pair. A
		// false goal has neither member, so omission and an implied default can
		// never acquire producer semantics accidentally.
		for goal in goals {
			let matchingGoals = [for candidate in goals if candidate.id == goal.id {candidate}]
			let matchingRequirements = [for requirement in deliverable_requirements if requirement.referee_goal_id == goal.id {requirement}]
			let matchingObligations = [for obligation in production_obligations if obligation.referee_goal_id == goal.id {obligation}]
			if len(matchingGoals) != 1 {
				_duplicate_goal_identity: _|_
			}
			if goal.requires_deliverable {
				if len(matchingRequirements) != 1 || len(matchingObligations) != 1 {
					_incomplete_producer_pair: _|_
				}
				if len(matchingRequirements) == 1 && len(matchingObligations) == 1 {
					if matchingObligations[0].deliverable_requirement_id != matchingRequirements[0].id {
						_uncrosslinked_producer_pair: _|_
					}
					if matchingRequirements[0].production_obligation_id != matchingObligations[0].id {
						_uncrosslinked_deliverable_requirement: _|_
					}
					if matchingObligations[0].deliverable != matchingRequirements[0].deliverable {
						_mismatched_producer_deliverable: _|_
					}
				}
			}
			if !goal.requires_deliverable && (len(matchingRequirements) != 0 || len(matchingObligations) != 0) {
				_false_goal_has_producer_pair: _|_
			}
		}

		// Every pair member resolves back to one true referee goal, and every
		// identity is single-valued within this preview body.
		for requirement in deliverable_requirements {
			let matchingGoals = [for goal in goals if goal.id == requirement.referee_goal_id {goal}]
			let matchingRequirements = [for candidate in deliverable_requirements if candidate.id == requirement.id {candidate}]
			if len(matchingGoals) != 1 || !matchingGoals[0].requires_deliverable {
				_orphaned_deliverable_requirement: _|_
			}
			if len(matchingRequirements) != 1 {
				_duplicate_deliverable_requirement_identity: _|_
			}
		}
		for obligation in production_obligations {
			let matchingGoals = [for goal in goals if goal.id == obligation.referee_goal_id {goal}]
			let matchingRequirements = [for requirement in deliverable_requirements if requirement.id == obligation.deliverable_requirement_id {requirement}]
			let matchingObligations = [for candidate in production_obligations if candidate.id == obligation.id {candidate}]
			let assignedClaims = [for item in patch_plan for obligationID in item.production_obligation_ids if obligationID == obligation.id {obligationID}]
			if len(matchingGoals) != 1 || !matchingGoals[0].requires_deliverable || len(matchingRequirements) != 1 {
				_orphaned_production_obligation: _|_
			}
			if len(matchingRequirements) == 1 && (matchingRequirements[0].referee_goal_id != obligation.referee_goal_id || matchingRequirements[0].deliverable != obligation.deliverable) {
				_mismatched_production_obligation: _|_
			}
			if len(matchingObligations) != 1 {
				_duplicate_production_obligation_identity: _|_
			}
			if len(assignedClaims) != 1 {
				_inexact_patch_plan_partition: _|_
			}
			for link in obligation.replacement_links {
				if link.replaces_obligation_id == obligation.id {
					_self_replacing_production_obligation: _|_
				}
			}
		}
		for item in patch_plan {
			let matchingItems = [for candidate in patch_plan if candidate.item_id == item.item_id {candidate}]
			if len(matchingItems) != 1 {
				_duplicate_patch_plan_item_identity: _|_
			}
			for obligationID in item.production_obligation_ids {
				if len([for obligation in production_obligations if obligation.id == obligationID {obligation}]) != 1 {
					_fabricated_patch_plan_assignment: _|_
				}
			}
		}
		...
	}
	cross_links!: [...{...}]
})

#RootTechnicalApproachTreeEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!: "TechnicalApproachTree"
	variant!: "patch-series-root"
	body!: #RootTechnicalApproachTree
})

#PatchPlanWorkItem: close({
	id!:                  string
	objective!:           string
	named_content!:       [...close({name!: string, kind!: string})]
	acceptance_criteria!: [...string]
	how_verified!:        string
	depends_on!:          [...string]
	production_obligation_ids?: [...string & != ""]
})

#PatchPlanSlice: close({
	id!: string
	items!: [...#PatchPlanWorkItem]
	deferred!: [...close({item!: string, why_safe_to_defer!: string})]
	open_questions!: [...string]
})

#ChildTechnicalApproachTree: close({
	lineage_child!: close({
		id!:               string
		lifecycle_status!: string
		summary!:          string
		systems!: [...string]
		acceptance_criteria!: [...string]
		functional_check!: string
		ux_acceptance_tests!: [...string]
		risks!: [...string]
		patch_plan?: #PatchPlanSlice
	})
})

#ChildTechnicalApproachTreeEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "TechnicalApproachTree"
	variant!:    "patch-series-child"
	body!:       #ChildTechnicalApproachTree
})

#LineageCarrierRecipe: close({
	source_context!:     string
	source_kind!:        string
	target_context!:     string
	target_kind!:        string
	projection_profile!: string
	schema_profile!:     string
	blob_sha256!:        string
	schema_sha256!:      string
})

#LineageCarrierRecipeEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "LineageCarrierRecipe"
	variant!:    "network-publication"
	body!:       #LineageCarrierRecipe
})

// Contributor handoff records move as one compatibility boundary. The CUE
// records below are durable; JSON/schema/carrier renderings are projections.
#QuestionsThePatchMustAnswer: close({
	questions_the_patch_must_answer!: [...{objection_id!: string & !="", ...}]
})

#SeriesQuestionsEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "QuestionsThePatchMustAnswer"
	variant!:    "patch-series"
	body!:       #QuestionsThePatchMustAnswer
})

#ContributionQuestionsEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "QuestionsThePatchMustAnswer"
	variant!:    "contribution"
	body!:       #QuestionsThePatchMustAnswer
})

#ImplementationExperienceReport: close({
	implementation_experience_reports!: [...{
		objection_id!:           string & !=""
		claim!:                  string
		executable_check!:       string
		check_failure_evidence!: string
		invalidated_node_ids!: [...string]
		contingency_plan!:            string
		rewind_class!:                "requires_direction_change"
		reported_from_node_key!:      string
		processed_in_author_version?: int & >=1
	}]
})

#ImplementationExperienceReportEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ImplementationExperienceReport"
	variant!:    "patch-series"
	body!:       #ImplementationExperienceReport
})

#ImplementationResultContract: close({
	schema_version!: "1"
	authority_path!: "implementation-result.cue"
	computed_by!:    "patch-author-harness"
	required_fields!: ["patch_series_branch", "node_key", "acknowledged_patchwork_checks"]
	require_complete_must_answer_outcomes!: true
	generated_json_temporary!:              true
	generated_schema_temporary!:            true
})

#ImplementationResultContractEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ImplementationResultContract"
	variant!:    "contributor-contract"
	body!:       #ImplementationResultContract
})

#ImplementationResult: close({
	patch_series_branch!: string & !=""
	node_key!:            string & !=""
	// Patchwork values are typed facts. Containers would erase the exact
	// scalar type comparison performed again by functional acceptance.
	acknowledged_patchwork_checks!: {[string]: bool | number | string | null}
	// Every admitted outcome is complete enough to make a decision; the
	// expected key set is bound by the series Questions body at the carrier.
	must_answer_outcomes?: {[string]: {outcome!: string & !="", ...}}
})

#ImplementationResultEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ImplementationResult"
	variant!:    "harness-authored"
	body!:       #ImplementationResult
})

#FunctionalCheckCarrierRecipe: close({
	source_context!:               "implementation-result-v1"
	source_api_version!:           "ai-org-cue-body-v1"
	source_kind!:                  "ImplementationResult"
	source_variant!:               "harness-authored"
	target_context!:               "functional-acceptance-v1"
	target_api_version!:           "ai-org-cue-body-v1"
	target_kind!:                  "FunctionalAcceptanceVerdict"
	target_variant!:               "contribution"
	target!:                       "functional-acceptance"
	projection_profile!:           "consumer-json-v1"
	schema_profile!:               "codex-structured-output-v1"
	validation_precedes_dispatch!: true
	complete_outcomes!:            true
	preserve_failed_worktree!:     true
	carrier_output_temporary!:     true
})

#FunctionalCheckCarrierRecipeEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "FunctionalCheckCarrierRecipe"
	variant!:    "contributor-handoff"
	body!:       #FunctionalCheckCarrierRecipe
})

// The producer lifecycle is registered as one dormant compatibility matrix.
// These bodies can be parsed and vetted before the lifecycle cutover, but this
// admission does not make a canonical root authorable. All records are closed:
// routing taxonomies and eligibility-text surrogates therefore cannot become
// a second assignment authority.
#ProducerLifecycleOID:                string & =~"^[0-9a-f]{40,64}$"
#ProducerLifecycleSHA256:             string & =~"^[0-9a-f]{64}$"
#ProducerLifecycleSeriesBranch:       string & =~"^ai-org/patch-series/"
#ProducerLifecycleNodePath:           "." | (string & =~"^sub/[a-z0-9_]+$")
#ProducerLifecycleContributionBranch: string & =~"^ai-org/contrib/"
#ProducerIdentity: close({
	name!:  string & !=""
	email!: string & =~"^[^@]+@users\\.noreply\\.github\\.com$"
})
#ProducerSourceSnapshot: close({
	series_snapshot_oid!:             #ProducerLifecycleOID
	canonical_root_body_sha256!:      #ProducerLifecycleSHA256
	scope_decomposition_commit_oid!:  #ProducerLifecycleOID
	scope_decomposition_body_sha256!: #ProducerLifecycleSHA256
})
#ProducerPromiseBinding: close({
	obligation_id!:       string & !=""
	evidence_coordinate!: string & !=""
	promise_commit_oid!:  #ProducerLifecycleOID
	promise_body_sha256!: #ProducerLifecycleSHA256
})

// Empty objects are intentional only before a commitment or completion exists.
#ProducerCommitmentSlot: *close({}) | close({
	task_binding_path!:        string & !=""
	task_binding_body_sha256!: #ProducerLifecycleSHA256
})
#ProducerCompletionClaimSlot: *close({}) | close({
	assertion_path!:        string & !=""
	assertion_body_sha256!: #ProducerLifecycleSHA256
})

#ProducerPromise: close({
	series_snapshot_oid!:         #ProducerLifecycleOID
	series_branch!:               #ProducerLifecycleSeriesBranch
	node_path!:                   #ProducerLifecycleNodePath
	contribution_branch!:         #ProducerLifecycleContributionBranch
	canonical_root_body_sha256!:  #ProducerLifecycleSHA256
	referee_goal_id!:             string & !=""
	obligation_id!:               string & !=""
	deliverable!:                 string & !=""
	eligibility_predicate!:       string & !=""
	producer!:                    #ProducerIdentity
	eligibility_decision!:        "eligible" | "ineligible"
	eligibility_basis!:           string & !=""
	commitment_slot!:             #ProducerCommitmentSlot
	completion_claim_slot!:       #ProducerCompletionClaimSlot
	previous_promise_commit_oid!: null | #ProducerLifecycleOID
	authored_at!:                 string & !=""
})

#ProducerPromiseEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ProducerPromise"
	variant!:    "producer-commitment"
	body!:       #ProducerPromise
})

#ProducerTaskBindingTask: close({
	item_id!: string & !=""
	// Every binding is one assignment edge. A patch-plan item with several
	// obligations receives several independently admitted bindings.
	production_obligation_ids!: [string & !=""]
})

#ProducerTaskBinding: close({
	binding_id!:                      string & !=""
	series_branch!:                   #ProducerLifecycleSeriesBranch
	node_path!:                       #ProducerLifecycleNodePath
	contribution_branch!:             #ProducerLifecycleContributionBranch
	series_snapshot_oid!:             #ProducerLifecycleOID
	canonical_root_body_sha256!:      #ProducerLifecycleSHA256
	scope_decomposition_commit_oid!:  #ProducerLifecycleOID
	scope_decomposition_body_sha256!: #ProducerLifecycleSHA256
	producer!:                        #ProducerIdentity
	tasks!: [#ProducerTaskBindingTask, ...#ProducerTaskBindingTask]
	promise_bindings!: [#ProducerPromiseBinding, ...#ProducerPromiseBinding]
})

#ProducerTaskBindingEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ProducerTaskBinding"
	variant!:    "contribution-task"
	body!:       #ProducerTaskBinding
})

#ProducerAssertionEvidence: close({
	description!:       string & !=""
	artifact_anchor!:   string & !=""
	artifact_blob_oid!: #ProducerLifecycleOID
	sha256!:            #ProducerLifecycleSHA256
})
#ProducerAssertion: close({
	obligation_id!:   string & !=""
	referee_goal_id!: string & !=""
	claim!:           string & !=""
	means!:           string & !=""
	evidence!: [#ProducerAssertionEvidence, ...#ProducerAssertionEvidence]
})

#ProducerCompletionAssertion: close({
	series_branch!:                   #ProducerLifecycleSeriesBranch
	node_path!:                       #ProducerLifecycleNodePath
	contribution_branch!:             #ProducerLifecycleContributionBranch
	producer!:                        #ProducerIdentity
	series_snapshot_oid!:             #ProducerLifecycleOID
	canonical_root_body_sha256!:      #ProducerLifecycleSHA256
	scope_decomposition_commit_oid!:  #ProducerLifecycleOID
	scope_decomposition_body_sha256!: #ProducerLifecycleSHA256
	task_binding_commit_oid!:         #ProducerLifecycleOID
	task_binding_body_sha256!:        #ProducerLifecycleSHA256
	implementation_oid!:              #ProducerLifecycleOID
	promise_bindings!: [#ProducerPromiseBinding, ...#ProducerPromiseBinding]
	assertions!: [#ProducerAssertion, ...#ProducerAssertion]
	asserted_at!: string & !=""
})

#ProducerCompletionAssertionEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ProducerCompletionAssertion"
	variant!:    "producer-claim"
	body!:       #ProducerCompletionAssertion
})

#ClaimAdmissionAssertion: close({
	obligation_id!:         string & !=""
	assertion_commit_oid!:  #ProducerLifecycleOID
	assertion_body_sha256!: #ProducerLifecycleSHA256
})
#ClaimAdmissionRejection: close({
	type!:       "missing-promise" | "missing-binding" | "missing-assertion" | "digest-mismatch" | "source-mismatch" | "result-mismatch"
	coordinate!: string & !=""
	detail!:     string & !=""
})

#ClaimAdmission: close({
	evaluated_contribution_oid!: #ProducerLifecycleOID
	evaluated_at!:               string & !=""
	source_snapshot!:            #ProducerSourceSnapshot
	authority_notes_ref_oid!:    #ProducerLifecycleOID
	required_obligation_ids!: [string & !="", ...string & !=""]
	promise_bindings!: [#ProducerPromiseBinding, ...#ProducerPromiseBinding]
	task_binding_body_sha256!: #ProducerLifecycleSHA256
	assertions!: [#ClaimAdmissionAssertion, ...#ClaimAdmissionAssertion]
	implementation_result_body_sha256!: #ProducerLifecycleSHA256
	admitted!:                          bool
	rejections?: [#ClaimAdmissionRejection, ...#ClaimAdmissionRejection]
})

#ClaimAdmissionEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "ClaimAdmission"
	variant!:    "functional-acceptance"
	body!:       #ClaimAdmission
})

#FunctionalRefereeEvaluation: close({
	referee_goal_id!: string & !=""
	accepted!:        bool
	finding!:         string & !=""
	evidence!:        string & !=""
})

#FunctionalAcceptanceVerdict: close({
	evaluated_contribution_oid!:  #ProducerLifecycleOID
	claim_admission_body_sha256!: #ProducerLifecycleSHA256
	required_obligation_ids!: [string & !="", ...string & !=""]
	source_snapshot!:         #ProducerSourceSnapshot
	authority_notes_ref_oid!: #ProducerLifecycleOID
	referee_evaluations!: [#FunctionalRefereeEvaluation, ...#FunctionalRefereeEvaluation]
	reachable!: bool
	blockers?: [string & !="", ...string & !=""]
	notes!:     string & !=""
	issued_at!: string & !=""
})

#FunctionalAcceptanceVerdictEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "FunctionalAcceptanceVerdict"
	variant!:    "contribution"
	body!:       #FunctionalAcceptanceVerdict
})

#AcceptanceAuthoritySeal: close({
	authority_contract_version!:  "acceptance-authority-v1"
	verifier_role!:               "independent-functional-acceptance"
	// Older retained seals predate the direct ref binding. They remain
	// parseable for immutable integration-link resolution, while every new
	// current-tip seal producer and predicate requires this field.
	contribution_ref?:            string & =~"^refs/heads/ai-org/contrib/"
	target_verdict_commit_oid!:   #ProducerLifecycleOID
	verdict_body_sha256!:         #ProducerLifecycleSHA256
	claim_admission_body_sha256!: #ProducerLifecycleSHA256
	source_snapshot!:             #ProducerSourceSnapshot
	authority_notes_ref_oid!:     #ProducerLifecycleOID
	issued_at!:                   string & !=""
})

#AcceptanceAuthoritySealEnvelope: close({
	apiVersion!: "ai-org-cue-body-v1"
	kind!:       "AcceptanceAuthoritySeal"
	variant!:    "verifier-authority"
	body!:       #AcceptanceAuthoritySeal
})
