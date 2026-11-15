package agent

import (
	"context"
	"encoding/json"

	actool "github.com/Autumn-27/norma/tool"
)

// This file turns code-only built-in tools into an enumerable catalog with database overrides:
//   - BuiltinToolSeeds() expands the three executing agents' built-in tools into seed records (key,
//     description, parameter schema and default agent bindings) for idempotent server startup seeding into tools.
//   - The ToolResolve hook filters assembled tools by agent, overrides descriptions/schemas and injects parameter
//     defaults from DB tools rows at runtime. Keys/handlers remain code-owned; the DB changes only prose and defaults.
// Handlers (Call behavior) always come from code; the DB can change only model-visible instructions and default arguments.

// ToolSeed is a seedable snapshot of a built-in tool: key is CoreTool.Name(), fixed to its handler and read-only
// in the UI. Desc/Schema come from code definitions; Agents contains the code-default agent bindings.
type ToolSeed struct {
	Key    string         // = CoreTool.Name(); immutable primary key.
	Desc   string         // Top-level description, overridable in the UI.
	Schema map[string]any // Parameter JSON Schema; structure read-only, descriptions/defaults editable in the UI.
	Agents []string       // Default bound agent keys (worker/planner/mainagent).
}

// builtinToolsByAgent builds each executing agent's domain tool set from a read-only shell ToolSet with nil stores.
// Constructors put closures into Spec without dereferencing stores during construction, so nil is safe.
// These tools are used ONLY to read Name()/Description()/InputSchema(), never to Call.
//
// Deliberately exclude SDK general tools actool.DefaultTools() (Read/Write/Edit/MultiEdit/LS/Glob/
// Grep/Bash): every agent always has them, with no binding choice. Most instructions live in Prompt(),
// whereas this table overrides only Description(), causing misleading partial overrides. No seed means no DB row:
// ToolResolve passes them through unchanged, preserving prior behavior. Only ARTEX domain tools enter the managed catalog.
func builtinToolsByAgent() map[string][]actool.CoreTool {
	ts := NewToolSet(nil, "")
	return map[string][]actool.CoreTool{
		"mainagent": ts.MainAgentTools(),
		"planner":   ts.PlannerTools(),
		"worker":    ts.WorkerTools(),
		// goals (the goal decomposer) binds set_goals + set_constraints by default to persist decomposed objectives
		// and extracted operational constraints. These managed tools are shared with mainagent and support UI description/schema/binding edits.
		"goals": {ts.setGoals(), ts.setConstraints()},
		// auto defaults to finding-reporting and asset-management tools; other domain tools may be selected in the UI.
		// New databases receive these seeds; seedAutoDefaultBindings migrates existing databases.
		"auto": {ts.addFinding(), ts.insertAssets(), ts.addCompanyScope(), ts.listAssets(), ts.listCompanies()},
		// pentest (standalone penetration-testing agent) defaults to asset lookup/insertion, finding reporting/lookup and company lookup.
		// New databases receive these seeds; seedPentestDefaultBindings migrates existing databases.
		"pentest": {ts.listAssets(), ts.insertAssets(), ts.addFinding(), ts.listFindings(), ts.listCompanies()},
	}
}

// defaultUnbound tools still enter the system catalog and remain visible/selectable in the UI, but
// bind to NO agent by default. ToolResolve excludes unbound tools for every agent until explicit opt-in.
// They remain in an agent's base tool set (for example goal_met in PlannerTools) so seeding can obtain
// their description/schema and ToolResolve can retain them at runtime after users manually bind them.
//
// goal_met declares the ENTIRE task complete without individual prove_goal calls. Its broad effect and misjudgment risk,
// plus overlap with automatic completion after proving the last goal, justify no default binding; bind manually when needed.
var defaultUnbound = map[string]bool{"goal_met": true}

// BuiltinToolSeeds deduplicates and merges agent tool sets into seeds: same-name tools (such as list_assets
// shared by several agents) become one entry with unioned Agents. defaultUnbound tools force empty bindings.
func BuiltinToolSeeds() []ToolSeed {
	byAgent := builtinToolsByAgent()
	order := []string{"mainagent", "goals", "planner", "worker", "auto", "pentest"}

	type acc struct {
		tool   actool.CoreTool
		agents []string
	}
	m := map[string]*acc{}
	var keys []string
	for _, ak := range order {
		for _, t := range byAgent[ak] {
			a, ok := m[t.Name()]
			if !ok {
				a = &acc{tool: t}
				m[t.Name()] = a
				keys = append(keys, t.Name())
			}
			a.agents = append(a.agents, ak)
		}
	}

	out := make([]ToolSeed, 0, len(keys))
	for _, k := range keys {
		a := m[k]
		agents := a.agents
		if defaultUnbound[k] {
			agents = []string{} // Catalogued and manually bindable, but default to no agent; store [] rather than null, consistent with other tools.
		}
		out = append(out, ToolSeed{
			Key:    k,
			Desc:   a.tool.Description(),
			Schema: a.tool.InputSchema(),
			Agents: agents,
		})
	}
	return out
}

// ToolResolve, if set, post-processes an agent's fully-assembled tool list against
// the DB tools table: it drops tools not bound to this agent (or globally disabled)
// and wraps the rest so the model sees the DB-overridden description/schema and
// default arguments get injected. Tools with no matching DB row (MCP/skill/host tools like
// traffic) pass through untouched. nil = tools unchanged. Wired in server/assembly.go.
var ToolResolve func(ctx context.Context, agentKey string, tools []actool.CoreTool) []actool.CoreTool

// DecorateTool wraps t so Description()/InputSchema() report the DB overrides and
// Call() injects scalar parameter defaults (from schema's "default" props) whenever
// the model omitted them. Name/Prompt/permission/scheduler flags delegate to t, so
// the tool's identity and handler are unchanged. Empty desc/schema fall back to t's.
func DecorateTool(t actool.CoreTool, desc string, schema map[string]any) actool.CoreTool {
	if desc == "" {
		desc = t.Description()
	}
	if len(schema) == 0 {
		schema = t.InputSchema()
	}
	return &overriddenTool{CoreTool: t, desc: desc, schema: schema}
}

// overriddenTool is a CoreTool decorator: it embeds the original (so all behavioral
// methods — Prompt/IsReadOnly/IsConcurrencySafe/CheckPermissions/Name — delegate)
// and overrides only the model-facing description/schema plus default injection.
type overriddenTool struct {
	actool.CoreTool
	desc   string
	schema map[string]any
}

func (o *overriddenTool) Description() string         { return o.desc }
func (o *overriddenTool) InputSchema() map[string]any { return o.schema }

func (o *overriddenTool) Call(ctx context.Context, in json.RawMessage, tc *actool.ToolContext) (actool.Result, error) {
	return o.CoreTool.Call(ctx, injectDefaults(in, o.schema), tc)
}

// injectDefaults fills scalar parameter defaults declared in the (possibly edited)
// schema into the input JSON whenever the model omitted the field or left it empty/
// null. Structure (names/types/required) is untouched; only default values are merged in.
func injectDefaults(in json.RawMessage, schema map[string]any) json.RawMessage {
	defs := scalarDefaults(schema)
	if len(defs) == 0 {
		return in
	}
	m := map[string]json.RawMessage{}
	if len(in) > 0 {
		if err := json.Unmarshal(in, &m); err != nil {
			return in // non-object input: don't touch it
		}
	}
	changed := false
	for k, dv := range defs {
		if cur, ok := m[k]; !ok || isEmptyJSON(cur) {
			m[k] = dv
			changed = true
		}
	}
	if !changed {
		return in
	}
	b, err := json.Marshal(m)
	if err != nil {
		return in
	}
	return b
}

// scalarDefaults extracts properties[k]["default"] for scalar params (string/
// integer/number/boolean). Array/object defaults are skipped: merging them is
// ambiguous and not worth the surprise.
func scalarDefaults(schema map[string]any) map[string]json.RawMessage {
	props, _ := schema["properties"].(map[string]any)
	if len(props) == 0 {
		return nil
	}
	out := map[string]json.RawMessage{}
	for name, raw := range props {
		p, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		dv, ok := p["default"]
		if !ok || dv == nil {
			continue
		}
		switch p["type"] {
		case "string", "integer", "number", "boolean":
			if b, err := json.Marshal(dv); err == nil {
				out[name] = b
			}
		}
	}
	return out
}

func isEmptyJSON(raw json.RawMessage) bool {
	s := string(raw)
	return s == "null" || s == `""`
}
