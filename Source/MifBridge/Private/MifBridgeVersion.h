// Engine-version guards.
//
// MifBridge is built on a cooked UE 5.3.2 editor and is ALSO run daily on UE 5.7 (Curfew). Until this
// header there was NO version-guard facility anywhere in the source - not one ENGINE_MINOR_VERSION -
// which meant the only available answers to "this API differs between engines" were to use the common
// subset or to leave the feature out entirely. Mover was left out for exactly that reason.
//
// Read docs/02_GOTCHAS.md section 14 first. The short version is that the trap runs in BOTH directions:
//
//   * 5.3 has it, 5.7 DELETED it   - GetAssetsByClass(FName), IsPendingKillOrUnreachable. A 5.3
//                                    deprecation WARNING is a 5.7 build BREAK.
//   * 5.7 has it, 5.3 NEVER DID    - UGameFeaturesSubsystem::GetPluginState, ForEachGameFeature. No
//                                    warning fires, because nothing was deprecated. Compiles clean on
//                                    5.7, fails outright on 5.3.
//
// The second direction is the dangerous one, and it is the reason this header exists.
//
// ============================================================================================
// USE THIS SPARINGLY, AND PREFER THE COMMON SUBSET.
// ============================================================================================
//
// A guarded branch is code that only ONE of the two builds ever compiles, which means the other branch
// is unverified until someone builds on that engine. That is a real cost: every 5.7-only branch written
// here is untested from this machine. So the order of preference is:
//
//   1. Use an API present in both trees. Verify it in BOTH and record the line numbers.
//   2. If the newer engine has a BETTER answer, use the common subset as the baseline and let the guard
//      ADD to it - never let the two branches produce differently-shaped output.
//   3. Only if a feature is impossible on the older engine, guard the whole thing out - and make the
//      older build REFUSE by name rather than silently omit the endpoint, the same way MIF_WITH_*
//      handles an absent plugin.
//
// Rule 2 matters most. If 5.3 returns one set of fields and 5.7 returns another, every caller has to
// branch on engine version, and the bridge has exported its problem to its consumers instead of solving
// it. Keep the shape identical; let the newer engine fill in more.

#pragma once

#include "Runtime/Launch/Resources/Version.h"

// True when the running engine is at least Major.Minor. Written as a nested comparison rather than a
// packed integer so a wrong answer is obvious on sight.
#define MIF_ENGINE_AT_LEAST(Major, Minor) \
	((ENGINE_MAJOR_VERSION > (Major)) || \
	 (ENGINE_MAJOR_VERSION == (Major) && ENGINE_MINOR_VERSION >= (Minor)))

// True when the running engine is older than Major.Minor. Deliberately provided rather than leaving
// callers to write !MIF_ENGINE_AT_LEAST, because the negation reads badly in a preprocessor condition
// and is easy to misplace.
#define MIF_ENGINE_BEFORE(Major, Minor) (!MIF_ENGINE_AT_LEAST(Major, Minor))

// Convenience for the two engines this plugin actually targets, so the intent is legible at the use
// site: MIF_ENGINE_5_7_PLUS says "the newer of our two", which is what the code usually means.
#define MIF_ENGINE_5_7_PLUS MIF_ENGINE_AT_LEAST(5, 7)
