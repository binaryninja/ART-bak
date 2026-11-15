package agent

import (
	"log"
	"path/filepath"

	"github.com/Autumn-27/norma/agentcore"
	"github.com/Autumn-27/norma/noaadapter"
)

// noaWarn returns a diagnostics sink tagging non-fatal noa messages with the
// session, routed through the package logger (agents have no per-instance one).
func noaWarn(session string) func(string) {
	return func(msg string) { log.Printf("[noa] %s: %s", session, msg) }
}

// noa is the model-driven context compaction mechanism introduced in norma v0.4.0, exposed as an experimental
// platform feature toggled in system settings. It is mutually exclusive with built-in compaction: noaadapter.Enable
// is the sole entry point, installing the Compactor, Compress tool and three persistent prompt sections together.
// Not calling Enable leaves it disabled and built-in compaction active. Each agent's noaEnabledFn resolves the switch
// once per run, so changes affect only subsequently started runs without rebuilding agents.

// enableNoa connects noa to opts when the resolver enables it. archiveRoot is the persistent root for original text
// (global workDir: all agents use <workDir>/noa, not separate task/intent directories). sessionID names
// its archive subdirectory and is globally unique, avoiding collisions within the shared root.
//
// noa is experimental: integration failure must not interrupt real tasks. Report via onWarn and fall back to built-in compaction.
// On success, clear opts.Compaction to avoid agentcore warnings about configuring two context managers.
func enableNoa(opts *agentcore.Options, enabled func() bool, archiveRoot, sessionID string, onWarn func(string)) {
	if enabled == nil || !enabled() {
		return
	}
	if opts.OnWarn == nil {
		opts.OnWarn = onWarn
	}
	if err := noaadapter.Enable(opts, noaadapter.Options{
		ArchiveBaseDir: filepath.Join(archiveRoot, "noa"),
		SessionID:      sessionID,
		OnWarn:         onWarn,
	}); err != nil {
		if onWarn != nil {
			onWarn("noa 压缩启用失败,回退内置压缩:" + err.Error())
		}
		return
	}
	// Compactor overrides Compaction, but agentcore warns whenever both are present; clear it explicitly.
	opts.Compaction = nil
}
