package db

import (
	"fmt"
	"net"
	"net/url"
	"strings"
)

// Asset-rule matching/enforcement. asset_intercept.go stores rules; this file
// matches target domain/IP/URL candidates against enabled rules. Agent tools
// (add_intent/insert_assets) call it before dispatch/insertion and reject hits.

// AssetInterceptKindLabel returns the localized kind label for agent explanations.
func AssetInterceptKindLabel(kind string) string {
	switch kind {
	case "exact_domain":
		return "域名(全等)"
	case "exact_ip":
		return "IP(全等)"
	case "exact_url":
		return "URL(全等)"
	case "fuzzy_domain":
		return "域名(模糊)"
	case "fuzzy_ip":
		return "IP(模糊)"
	case "fuzzy_url":
		return "URL(模糊)"
	case "cidr":
		return "CIDR 网段"
	}
	return kind
}

// Reason returns a readable rule hit with rule kind, pattern and optional note.
func (r AssetInterceptRule) Reason() string {
	s := fmt.Sprintf("命中资产拦截规则 [%s: %s]", AssetInterceptKindLabel(r.Kind), r.Pattern)
	if note := strings.TrimSpace(r.Note); note != "" {
		s += "（" + note + "）"
	}
	return s
}

// matchOne checks an enabled rule against domain/IP/URL candidates and returns the matched value.
func matchOne(r AssetInterceptRule, domains, ips, urls []string) (string, bool) {
	p := strings.TrimSpace(r.Pattern)
	if p == "" {
		return "", false
	}
	switch r.Kind {
	case "exact_domain":
		for _, d := range domains {
			if strings.EqualFold(strings.TrimSpace(d), p) {
				return d, true
			}
		}
	case "exact_ip":
		for _, ip := range ips {
			if strings.TrimSpace(ip) == p {
				return ip, true
			}
		}
	case "exact_url":
		for _, u := range urls {
			if strings.TrimSpace(u) == p {
				return u, true
			}
		}
	case "fuzzy_domain":
		lp := strings.ToLower(p)
		for _, d := range domains {
			if d != "" && strings.Contains(strings.ToLower(d), lp) {
				return d, true
			}
		}
	case "fuzzy_ip":
		for _, ip := range ips {
			if ip != "" && strings.Contains(ip, p) {
				return ip, true
			}
		}
	case "fuzzy_url":
		lp := strings.ToLower(p)
		for _, u := range urls {
			if u != "" && strings.Contains(strings.ToLower(u), lp) {
				return u, true
			}
		}
	case "cidr":
		_, ipnet, err := net.ParseCIDR(p)
		if err != nil {
			return "", false
		}
		for _, ip := range ips {
			if pip := net.ParseIP(strings.TrimSpace(ip)); pip != nil && ipnet.Contains(pip) {
				return ip, true
			}
		}
	}
	return "", false
}

// MatchAssetInterceptRules returns the first matching enabled rule and matched value.
// insert_assets uses it on raw inputs before persistence.
func MatchAssetInterceptRules(rules []AssetInterceptRule, domains, ips, urls []string) (AssetInterceptRule, string, bool) {
	for _, r := range rules {
		if !r.Enabled {
			continue
		}
		if v, ok := matchOne(r, domains, ips, urls); ok {
			return r, v, true
		}
	}
	return AssetInterceptRule{}, "", false
}

// interceptCandidates extracts domain/IP/URL candidates from a persisted asset.
// Classify URL hosts too, so URL-only services match domain/IP rules.
func (a *Asset) interceptCandidates() (domains, ips, urls []string) {
	add := func(dst *[]string, s string) {
		if s = strings.TrimSpace(s); s != "" {
			*dst = append(*dst, s)
		}
	}
	add(&domains, a.Domain)
	add(&domains, a.RootDomain)
	for _, d := range a.BoundDomains {
		add(&domains, d)
	}
	add(&ips, a.IP)
	add(&urls, a.URL)
	if a.URL != "" {
		if u, err := url.Parse(a.URL); err == nil {
			if h := u.Hostname(); h != "" {
				if net.ParseIP(h) != nil {
					add(&ips, h)
				} else {
					add(&domains, h)
				}
			}
		}
	}
	return domains, ips, urls
}

// InterceptLabel returns a short asset identifier for agent explanations.
func (a *Asset) InterceptLabel() string {
	var target string
	switch {
	case a.Domain != "":
		target = a.Domain
	case a.URL != "":
		target = a.URL
	case a.IP != "":
		target = a.IP
	default:
		target = fmt.Sprintf("#%d", a.ID)
	}
	return fmt.Sprintf("资产#%d[%s] %s", a.ID, a.Type, target)
}

// hasEnabledRule reports whether any rule is enabled.
func hasEnabledRule(rules []AssetInterceptRule) bool {
	for _, r := range rules {
		if r.Enabled {
			return true
		}
	}
	return false
}

// AssetGateDecision is the block-before-allow decision for candidate strings.
type AssetGateDecision struct {
	Allowed bool
	Reason  string // Rejection reason without asset identity; empty when Allowed=true.
}

// EvaluateAssetGate applies the task gate:
// 1. Any enabled blockRules match rejects with its reason.
// 2. Otherwise enabled allowRules with no match reject as outside allowed scope.
// 3. Otherwise allow.
//
// Empty/no-enabled allowRules disable allowlisting, permitting all candidates
// so an unconfigured allowlist does not accidentally reject every asset.
func EvaluateAssetGate(blockRules, allowRules []AssetInterceptRule, domains, ips, urls []string) AssetGateDecision {
	if rule, _, ok := MatchAssetInterceptRules(blockRules, domains, ips, urls); ok {
		return AssetGateDecision{Allowed: false, Reason: rule.Reason()}
	}
	if hasEnabledRule(allowRules) {
		if _, _, ok := MatchAssetInterceptRules(allowRules, domains, ips, urls); !ok {
			return AssetGateDecision{Allowed: false, Reason: "不在任务允许(白名单)范围内，不允许测试"}
		}
	}
	return AssetGateDecision{Allowed: true}
}

// AssetInterceptHit identifies an asset rejected by a block hit or missing allow match.
type AssetInterceptHit struct {
	Asset  *Asset
	Reason string // Readable reason
}

// Describe combines asset identity and reason.
func (h AssetInterceptHit) Describe() string {
	return fmt.Sprintf("%s → %s", h.Asset.InterceptLabel(), h.Reason)
}

// ListAssetInterceptRules delegates to DB so callers holding only AssetStore
// (such as agent tools) can read rules.
func (s *AssetStore) ListAssetInterceptRules() ([]AssetInterceptRule, error) {
	return s.db.ListAssetInterceptRules()
}

// CheckAssetsIntercept loads IDs and applies block-before-allow to each, returning
// rejections. Block rules are global union task-block; allow rules are this task only.
// No IDs returns immediately. Global GetByIDs prevents task scope from weakening the gate.
func (s *AssetStore) CheckAssetsIntercept(taskID int64, ids []int64) ([]AssetInterceptHit, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	blockRules, err := s.db.ListAssetInterceptRules()
	if err != nil {
		return nil, err
	}
	var allowRules []AssetInterceptRule
	if taskID > 0 {
		tb, ta, err := s.TaskInterceptRulesSplit(taskID)
		if err != nil {
			return nil, err
		}
		blockRules = append(blockRules, tb...)
		allowRules = ta
	}
	// No block or enabled allow rules: all assets pass without evaluation.
	if len(blockRules) == 0 && !hasEnabledRule(allowRules) {
		return nil, nil
	}
	assets, err := s.GetByIDs(ids)
	if err != nil {
		return nil, err
	}
	var hits []AssetInterceptHit
	for _, a := range assets {
		domains, ips, urls := a.interceptCandidates()
		if d := EvaluateAssetGate(blockRules, allowRules, domains, ips, urls); !d.Allowed {
			hits = append(hits, AssetInterceptHit{Asset: a, Reason: d.Reason})
		}
	}
	return hits, nil
}
