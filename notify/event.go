package notify

// Snapshot is the notification_events.snapshot JSONB contract. Finding writes
// produce it transactionally; server delivery/filtering consume it. It belongs to
// the notification domain: the DB serializes fields without interpreting them.
//
// Store event-time finding fields rather than rereading renamed/reclassified
// findings: later edits could misleadingly lower severity. Snapshot fan-out and
// rendering also avoid JOINs across findings, tasks and assets.
type Snapshot struct {
	// Event kind: finding_created / finding_status_changed
	Kind      string  `json:"kind"`
	FindingID int64   `json:"finding_id"`
	TaskID    int64   `json:"task_id"`
	VulnClass string  `json:"vulnclass"`
	Name      string  `json:"name"`
	Severity  string  `json:"severity"`
	Summary   string  `json:"summary"`
	AssetIDs  []int64 `json:"asset_ids"`
	// Nonempty only for kind=finding_status_changed.
	FromStatus string `json:"from_status,omitempty"`
	ToStatus   string `json:"to_status,omitempty"`
}

// Item is a finding ready for channel rendering.
type Item struct {
	FindingID int64
	Name      string
	VulnClass string
	Severity  string
	Summary   string
	// Assets are resolved display names (domains/IPs), populated by the server;
	// this package has no database access.
	Assets []string
	// DetailURL links to finding details; omitted when public_base_url is not configured.
	DetailURL string
	// Status-change events only: render Pending -> Fixed when both values are present.
	FromStatus string
	ToStatus   string
}

// IsStatusChange reports whether the item represents a status transition.
func (i Item) IsStatusChange() bool { return i.FromStatus != "" || i.ToStatus != "" }

// Title prefers a user-provided name, falls back to vulnclass, and uses a
// placeholder when both are empty; never return an empty title.
func (i Item) Title() string {
	if i.Name != "" {
		return i.Name
	}
	if i.VulnClass != "" {
		return i.VulnClass
	}
	return "(Unnamed finding)"
}

// Message is the complete payload for one channel send.
type Message struct {
	// A single notification has one item; a digest has an entire batch.
	// The caller must supply at least one item.
	Items []Item
	// Batch=true renders a digest with a different title, time window and count.
	Batch bool
	// WindowMinutes is the digest period used in Last N minutes text for batches.
	// Supply it explicitly instead of computing time.Since to keep rendering deterministic.
	WindowMinutes int
	// HomeURL is the dashboard URL (global public_base_url); empty omits the link.
	HomeURL string
}
